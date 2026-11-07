"""Independent accepted-handshake audit. No Verilator/driver dependencies."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path


class AuditError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise AuditError(message)


def number(value):
    return int(value, 0) if isinstance(value, str) else int(value)


def read_bytes(path):
    path = Path(path)
    return gzip.open(path, 'rb').read() if path.suffix == '.gz' else path.read_bytes()


def events(path):
    path = Path(path)
    opener = gzip.open if path.suffix == '.gz' else open
    last_cycle, last_seq = -1, -1
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            event = json.loads(line)
            cycle, seq = number(event['cycle']), number(event['seq'])
            require(cycle >= last_cycle and seq > last_seq,
                    f'{path}:{line_number}: nonmonotonic cycle/seq')
            event['cycle'], event['seq'] = cycle, seq
            last_cycle, last_seq = cycle, seq
            yield event


def payload(event, width):
    value = event.get('data_hex', event.get('data16'))
    require(value is not None, f"missing data at cycle {event['cycle']}")
    data = bytes.fromhex(value) if isinstance(value, str) else bytes(map(number, value))
    require(len(data) == width, 'data must contain one complete wire beat')
    return data


class RawAudit:
    def __init__(self, beat_bytes=16, initial=None, memory_base=0x80000000,
                 on_first=None, on_ack=None, on_write=None, retain_transactions=True):
        self.width = beat_bytes
        self.memory = bytearray(initial) if initial is not None else None
        self.memory_base = memory_base
        self.on_first, self.on_ack, self.on_write = on_first, on_ack, on_write
        self.live, self.generations = {}, Counter()
        self.counts = Counter()
        self.commands, self.attempts, self.arrivals = {}, {}, []
        self.pending_command = None
        self.transactions = []
        self.retain_transactions = retain_transactions
        self.drained_cycle = None
        self.last_channel_cycle = {}

    def memory_slice(self, address, length):
        offset = address - self.memory_base
        require(self.memory is not None and 0 <= offset and offset + length <= len(self.memory),
                f'out-of-aperture memory range {address:#x}+{length}')
        return self.memory[offset:offset + length]

    def annotation(self, event, tx, beat, expected, last, channel):
        aliases = (('txid', tx['id']), ('transaction', tx['id']),
                   ('source_gen', tx['generation']), ('beat_index', beat), ('beat', beat),
                   ('expected_beats', expected), ('first_a_cycle', tx['first_a']))
        for key, value in aliases:
            if key in event:
                require(number(event[key]) == value, f'{channel}: annotation {key} mismatch')
        for key, value in (('first', beat == 0), ('last', last), (f'last_{channel.lower()}', last)):
            if key in event:
                require(bool(event[key]) == value, f'{channel}: {key} mismatch')

    @staticmethod
    def command(event):
        inner = event.get('command', event)
        return tuple(number(inner[key]) for key in ('tag', 'funct', 'rs1', 'rs2'))

    def consume(self, event):
        require(self.drained_cycle is None, 'engine event after terminal drain')
        kind, cycle = event['event'], event['cycle']
        if kind in ('tl_a', 'tl_d', 'rocc_accept', 'cmd_accept', 'command_accepted'):
            channel = 'command' if kind not in ('tl_a', 'tl_d') else kind
            require(self.last_channel_cycle.get(channel) != cycle, f'multiple accepted {channel} beats in one cycle')
            self.last_channel_cycle[channel] = cycle
        if kind in ('rocc_try', 'cmd_attempt', 'rocc_attempt', 'command_attempt'):
            if event.get('valid', True) is False:
                return
            cmd = self.command(event)
            if self.pending_command is None:
                require(cmd[0] not in self.commands, 'reused accepted command tag')
                self.pending_command = cmd
                self.attempts[cmd[0]] = cycle
            else:
                require(cmd == self.pending_command, 'stalled command was replaced/reordered')
            return
        if kind in ('rocc_accept', 'cmd_accept', 'command_accepted'):
            cmd = self.command(event)
            require(cmd == self.pending_command, 'accepted command lacks identical pending attempt')
            require(cmd[0] not in self.commands, 'duplicate command acceptance')
            self.commands[cmd[0]] = {'cycle': cycle, 'payload': cmd}
            self.pending_command = None
            return
        if kind == 'arrival':
            self.arrivals.append(event)
            return
        if kind in ('engine_drained', 'run_end'):
            require(not self.live and self.pending_command is None, 'drained with live work')
            for key in ('live_sources', 'pending_responses', 'busy'):
                if key in event:
                    require(number(event[key]) == 0, f'run_end {key} is nonzero')
            if 'drained' in event:
                require(event['drained'] is True, 'run_end not drained')
            self.drained_cycle = cycle
            return
        if kind not in ('tl_a', 'tl_d'):
            return
        source, size = number(event['source']), number(event['size'])
        self.counts[kind] += 1
        if kind == 'tl_a':
            opcode, address = number(event['opcode']), number(event['address'])
            require(opcode in (0, 1, 4), 'unsupported A opcode')
            amount = 1 << size
            require(0 < amount <= 64 and address % amount == 0, 'bad A size/alignment')
            first = source not in self.live
            if first:
                self.generations[source] += 1
                self.counts['transactions'] += 1
                beats = max(1, amount // self.width)
                tx = {'id': self.counts['transactions'], 'source': source,
                      'generation': self.generations[source], 'opcode': opcode,
                      'address': address, 'size': size, 'bytes': amount,
                      'a_expected': 1 if opcode == 4 else beats,
                      'd_expected': beats if opcode == 4 else 1,
                      'a_count': 0, 'd_count': 0, 'first_a': cycle,
                      'last_a': None, 'writes': {}}
                self.live[source] = tx
                if opcode == 4 and self.memory is not None:
                    tx['read_snapshot'] = bytes(self.memory_slice(address, amount))
                if self.on_first:
                    self.on_first(tx, event)
            else:
                tx = self.live[source]
                require(tx['opcode'] in (0, 1) and tx['a_count'] < tx['a_expected'],
                        'premature source reuse or extra A beat')
                require((opcode, address, size) == (tx['opcode'], tx['address'], tx['size']),
                        'Put multibeat metadata changed')
            beat = tx['a_count']
            self.annotation(event, tx, beat, tx['a_expected'], beat + 1 == tx['a_expected'], 'A')
            lane_offset = address % self.width if amount < self.width else 0
            allowed = ((1 << min(amount, self.width)) - 1) << lane_offset
            mask = number(event['mask'])
            require(mask & ~allowed == 0, 'A mask outside transfer')
            if opcode in (0, 4):
                require(mask == allowed, 'PutFull/Get mask incomplete')
            if opcode in (0, 1):
                data = payload(event, self.width)
                beat_base = address // self.width * self.width + beat * self.width
                for lane in range(self.width):
                    if mask & (1 << lane):
                        byte_address = beat_base + lane
                        require(byte_address not in tx['writes'], 'duplicate byte within transaction')
                        tx['writes'][byte_address] = data[lane]
                        if self.memory is not None:
                            self.memory_slice(byte_address, 1)
                            self.memory[byte_address - self.memory_base] = data[lane]
                        if self.on_write:
                            self.on_write(tx, byte_address, data[lane], cycle)
                self.counts['put_payload_bytes'] += mask.bit_count()
            else:
                self.counts['get_payload_bytes'] += amount
            tx['a_count'] += 1
            if tx['a_count'] == tx['a_expected']:
                tx['last_a'] = cycle
            self.counts['max_live_sources'] = max(self.counts['max_live_sources'], len(self.live))
        else:
            require(source in self.live, 'D to non-live source')
            tx = self.live[source]
            opcode = number(event.get('raw_d_opcode', event.get('d_opcode', event['opcode'])))
            require(size == tx['size'] and opcode == (1 if tx['opcode'] == 4 else 0),
                    'D opcode/size does not match live A transaction')
            require(not event.get('denied', False) and not event.get('corrupt', False),
                    'denied/corrupt D')
            require(tx['last_a'] is not None and cycle >= tx['last_a'], 'D before final A')
            beat = tx['d_count']
            require(beat < tx['d_expected'], 'extra D beat')
            self.annotation(event, tx, beat, tx['d_expected'], beat + 1 == tx['d_expected'], 'D')
            if tx['opcode'] == 4 and self.memory is not None and 'data_hex' in event:
                data = payload(event, self.width)
                snapshot = tx['read_snapshot']
                if tx['bytes'] < self.width:
                    begin = tx['address'] % self.width
                    actual = data[begin:begin + tx['bytes']]
                    expected = snapshot
                else:
                    actual = data
                    expected = snapshot[beat * self.width:(beat + 1) * self.width]
                require(actual == expected, 'Get D data differs from independent memory snapshot')
            tx['d_count'] += 1
            if tx['d_count'] == tx['d_expected']:
                tx['last_d'] = cycle
                if tx['opcode'] in (0, 1):
                    self.counts['put_acks'] += 1
                    if self.on_ack:
                        self.on_ack(tx, event)
                if self.retain_transactions:
                    self.transactions.append(tx)
                del self.live[source]

    def finish(self):
        require(not self.live and self.pending_command is None, 'unfinished raw stream')
        require(self.drained_cycle is not None, 'missing final engine drain event')
        return dict(self.counts, accepted_commands=len(self.commands),
                    arrivals=len(self.arrivals), drained_cycle=self.drained_cycle)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('events')
    parser.add_argument('--initial')
    parser.add_argument('--final')
    parser.add_argument('--memory-base', type=lambda v: int(v, 0), default=0x80000000)
    parser.add_argument('--out')
    args = parser.parse_args()
    audit = RawAudit(initial=read_bytes(args.initial) if args.initial else None,
                     memory_base=args.memory_base)
    for event in events(args.events):
        audit.consume(event)
    report = audit.finish()
    if args.final:
        require(audit.memory is not None, '--final requires --initial')
        require(bytes(audit.memory) == read_bytes(args.final), 'complete final memory mismatch')
        report['complete_final_memory_equal'] = True
    report['events_sha256'] = hashlib.sha256(Path(args.events).read_bytes()).hexdigest()
    report['checker_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report['status'] = 'pass'
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
