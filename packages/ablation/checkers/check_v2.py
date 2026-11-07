"""Fail-closed, read-only audit for the response/busy controlled online runtime.

Only frozen inputs, public controller plans, accepted wire events and the final
memory dump are read.  No runtime/controller/generator modules are imported.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import sys

from raw_tl import AuditError, RawAudit, events, read_bytes, require


BASE = 0x80000000
MEMORY_BYTES = 16 * 1024 * 1024
FIXTURE_SHA = "e0e781aba066e8daa17bb43703d9f51786747e057d9bd2a7d9bc7356668ff109"
ROW_BYTES = {"c1": 3584, "a2": 896, "c2": 512}
REGIONS = {"a1": (BASE + 0x1000, 28672), "w1": (BASE + 0x8000, 802816),
           "c1": (BASE + 0xcc000, 172032), "a2": (BASE + 0xf8000, 14336),
           "c2": (BASE + 0xfc000, 8192), "w2": (BASE + 0x100000, 114688)}
METADATA = {"schedule_decision", "query_bind", "panel_snapshot", "group_retire",
            "query_c1_ready", "cohort_map", "query_fragment_release", "cohort_release",
            "restore_plan", "epoch_update", "input_release"}
FEES = {"issue": 1, "poll": 64, "counter_poll": 64, "status_poll": 64,
        "decision": 16, "response_decision": 16, "status_decision": 16,
        "delivery": 64, "sink": 64, "metadata": 16}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load(path):
    return json.loads(read_bytes(path).decode("utf-8-sig"))


def artifact(directory, name):
    found = [p for p in (directory / name, directory / (name + ".gz")) if p.is_file()]
    require(len(found) == 1, f"need exactly one artifact {name}[.gz]")
    return found[0]


def inside(address, length, names):
    return any(address >= REGIONS[k][0] and address + length <= sum(REGIONS[k]) for k in names)


def verify_inputs(directory, count):
    require(sha(directory / "manifest.json") == FIXTURE_SHA, "frozen fixture manifest differs")
    manifest, fixture = load(directory / "manifest.json"), load(directory / "fixture.json")
    require(manifest["status"] == "frozen_inputs_only", "fixture evidence class")
    for name, rec in manifest["files"].items():
        path = directory / name
        require(path.is_file() and path.stat().st_size == rec["bytes"] and sha(path) == rec["sha256"],
                f"frozen input hash/size differs: {name}")
    require(fixture["query_count"] == 32 and fixture["source_shape"] == [896, 896] and
            fixture["dependent_shape"] == [896, 128], "frozen shape differs")
    require(fixture["quantization"] == {"scale": "1/16", "rounding": "nearest_even", "saturation": [-128, 127]},
            "frozen quantization differs")
    with (directory / "arrivals.csv").open(newline="", encoding="ascii") as stream:
        arrivals = [{k: int(v) for k, v in row.items()} for row in csv.DictReader(stream)][:count]
    require([r["query"] for r in arrivals] == list(range(count)), "arrival identity/order")
    gold = {kind: read_bytes(directory / ("golden_" + kind + ".bin"))[:count * width]
            for kind, width in ROW_BYTES.items()}
    return arrivals, gold, read_bytes(directory / "memory.bin")


def unique_by(items, name, field):
    result = {}
    for event in items:
        if event["event"] == name:
            key = event[field]
            require(key not in result, f"duplicate {name} {key}")
            result[key] = event
    return result


def cpu_audit(controller):
    operations = [e for e in controller if e["event"] == "cpu_op"]
    require(operations, "missing paid CPU operations")
    ordered = sorted(operations, key=lambda e: (e["start"], e["end"]))
    previous_end = -1
    metadata_paid = Counter()
    for op in ordered:
        kind = op["kind"]
        require(kind in FEES, f"unregistered CPU fee category {kind}")
        require(op["end"] - op["start"] == FEES[kind] and op["cycle"] == op["end"],
                f"CPU fee duration/completion differs: {kind}")
        require(op["start"] >= previous_end, "paid CPU intervals overlap")
        previous_end = op["end"]
        if kind == "metadata":
            require(op["category"] in METADATA and op.get("cost", 16) == 16,
                    "unregistered metadata fee")
            metadata_paid[(op["end"], op["category"])] += 1
    metadata_claimed = Counter()
    for e in controller:
        if e["event"] == "metadata_op":
            require(e["category"] in METADATA and e["cost"] == 16, "metadata declaration differs")
            metadata_claimed[(e["cycle"], e["category"])] += 1
    require(metadata_paid == metadata_claimed and metadata_paid, "metadata declarations lack actual paid intervals")
    return operations, {kind: sum(e["end"] - e["start"] for e in operations if e["kind"] == kind)
                        for kind in sorted({e["kind"] for e in operations})}


class WireCommands:
    """Single-pass stable-command and stable-response handshake proof."""
    def __init__(self):
        self.tries, self.accepts, self.fires, self.captures = {}, {}, {}, []
        self.pending = None
        self.early_sample = None
        self.last_sample = None
        self.response = None
        self.response_last = None
        self.response_queue = []
        self.held_commands = self.held_responses = self.samples = 0

    @staticmethod
    def payload(e):
        return tuple(e[k] for k in ("tag", "funct", "rs1", "rs2", "rd", "xd"))

    def consume(self, e):
        kind, cycle = e["event"], e["cycle"]
        if kind == "rocc_try":
            tag = e["tag"]
            require(self.pending is None and tag not in self.tries, "overlapping/reused first command try")
            require(tag == len(self.tries) + 1 and e["cmd_id"] == tag, "command tag identity/order")
            require(e["rd"] == (1 if e["funct"] == 126 else 0) and e["xd"] is (e["funct"] == 126),
                    "command destination/response bit differs")
            self.pending, self.last_sample = e, cycle - 1
            self.tries[tag] = e
            if self.early_sample is not None:
                sample, self.early_sample = self.early_sample, None
                require(sample["cycle"] == cycle, "orphan pending sample before command try")
                self.consume(sample)
        elif kind == "rocc_pending_sample":
            if self.pending is None:
                require(self.early_sample is None, "orphan pending command sample")
                self.early_sample = e
                return
            require(self.pending is not None and cycle == self.last_sample + 1 and
                    self.payload(e) == self.payload(self.pending), "missing/changed held command sample")
            self.last_sample = cycle
            self.samples += 1
            self.held_commands += not e["ready"]
            self.sample_ready = e["ready"]
        elif kind == "rocc_accept":
            require(self.pending is not None and self.last_sample == cycle and self.sample_ready is True and
                    self.payload(e) == self.payload(self.pending), "accept lacks stable ready command")
            self.accepts[e["tag"]] = e
            if e["funct"] == 126:
                require(not self.response_queue, "multiple outstanding counter reads")
                self.response_queue.append(e)
            self.pending = None
        elif kind == "observer_counter_capture":
            self.captures.append(e)
        elif kind == "rocc_response_attempt":
            require(self.response_queue, "response without counter command")
            if self.response is None:
                self.response, self.response_last = e, cycle - 1
            require(cycle == self.response_last + 1 and (e["rd"], e["data"]) ==
                    (self.response["rd"], self.response["data"]), "held response changed or dropped")
            self.response_last, self.response_ready = cycle, e["ready"]
            self.held_responses += not e["ready"]
        elif kind == "rocc_response":
            require(self.response is not None and self.response_last == cycle and self.response_ready is True,
                    "response fire lacks ready presentation")
            command = self.response_queue.pop(0)
            require(e["tag"] == command["tag"] and e["rd"] == 1 and
                    (e["rd"], e["data"]) == (self.response["rd"], self.response["data"]) and
                    command["cycle"] <= cycle and 0 <= e["data"] < 2 ** 32,
                    "counter response identity/value")
            require(e["tag"] not in self.fires, "duplicate response fire")
            self.fires[e["tag"]] = e
            self.response = None

    def finish(self):
        require(self.pending is None and self.early_sample is None and self.response is None and not self.response_queue,
                "undrained command/response")
        counter = [e for e in self.accepts.values() if e["funct"] == 126]
        require(len(counter) == len(self.fires) >= 3, "counter response coverage")
        require([e["rs1"] for e in counter[:3]] == [2, 0x80005008, 0] and
                all(e["rs2"] == 0 for e in counter) and all(e["rs1"] == 0 for e in counter[3:]),
                "counter clear/config/read protocol differs")
        require(self.fires[counter[2]["tag"]]["data"] == 0, "initial counter baseline not zero")
        return {"accepted": len(self.accepts), "pending_samples": self.samples,
                "held_command_cycles": self.held_commands, "real_responses": len(self.fires),
                "held_response_cycles": self.held_responses}


class History:
    def __init__(self, controller, count, gold):
        self.count, self.gold = count, gold
        self.query = unique_by(controller, "query_plan", "query")
        self.bind = unique_by(controller, "query_bind", "query")
        self.lifecycle = {name: unique_by(controller, name, "query") for name in
                          ("c1_notified", "consumer_start", "a2_sealed", "c2_notified", "delivery_end", "query_release")}
        self.lifecycle["l2_start"] = {}
        for e in controller:
            if e["event"] == "l2_start":
                for query in e.get("queries", [e.get("query")]):
                    require(query is not None and query not in self.lifecycle["l2_start"], "L2 lifecycle identity")
                    self.lifecycle["l2_start"][query] = dict(e, query=query)
        wanted = set(range(count))
        require(set(self.query) == set(self.bind) == wanted, "query plan/bind coverage")
        for name, entries in self.lifecycle.items():
            require(set(entries) == wanted, f"missing query lifecycle: {name}")
        self.register = {kind: {} for kind in ("a2", "c2")}
        for e in controller:
            if e["event"] == "consumer_register":
                kind, query = e["kind"], e["query"]
                require(kind in self.register and query in wanted and query not in self.register[kind],
                        "consumer registration identity")
                require(e["bytes"] == ROW_BYTES[kind] and inside(e["base"], e["bytes"], [kind]) and
                        "cohort" in e and "generation" in e, "consumer allocation shape/generation")
                self.register[kind][query] = e
        require(all(set(r) == wanted for r in self.register.values()), "consumer registration coverage")
        self.data = {kind: [bytearray(ROW_BYTES[kind]) for _ in range(count)] for kind in ROW_BYTES}
        self.seen = {kind: [bytearray(ROW_BYTES[kind]) for _ in range(count)] for kind in ROW_BYTES}
        self.acked = {kind: [bytearray(ROW_BYTES[kind]) for _ in range(count)] for kind in ROW_BYTES}
        self.last_ack = {kind: [0] * count for kind in ROW_BYTES}
        self.last_ack_stage = {kind: [None] * count for kind in ROW_BYTES}
        self.panels = unique_by(controller, "panel_reserve", "panel")
        require(self.panels, "missing static panel reservation")
        self.panel_bytes, self.panel_acks, self.panel_last_ack = Counter(), Counter(), Counter()
        self.panel_seen = {}
        for pid, panel in self.panels.items():
            ranges = panel["expected_puts"]
            require(ranges and all(a < b and inside(a, b-a, ["c1"]) for a,b in ranges),
                    "panel static output range differs")
            ordered = sorted(ranges)
            require(all(b <= c for (_,b),(c,_) in zip(ordered, ordered[1:])), "panel static ranges overlap")
            self.panel_seen[pid] = bytearray(sum(b-a for a,b in ranges))
        for q, plan in self.query.items():
            require(plan["panel_count"] == 14 and 0 <= plan["slot"] < 16, "query finite panel/slot shape")
            fragments = sorted(plan["fragments"], key=lambda f: f["logical_offset"])
            offset = 0
            require(1 <= len(fragments) <= 2, "C1 descriptor fragment count")
            for fragment in fragments:
                require(fragment["logical_offset"] == offset and fragment["length"] > 0 and
                        inside(fragment["address"], fragment["length"], ["c1"]) and
                        "buffer" in fragment and "buffer_generation" in fragment, "C1 descriptor range/generation")
                offset += fragment["length"]
            require(offset == ROW_BYTES["c1"], "C1 descriptor does not cover exactly one logical row")

    def owners(self, kind, address, cycle):
        owners = []
        for q in range(self.count):
            if kind == "c1":
                if not self.query[q]["cycle"] <= cycle < self.lifecycle["a2_sealed"][q]["cycle"]:
                    continue
                for f in self.query[q]["fragments"]:
                    if f["address"] <= address < f["address"] + f["length"]:
                        owners.append((q, f["logical_offset"] + address - f["address"]))
            else:
                r = self.register[kind][q]
                end_name = "a2_sealed" if kind == "a2" else "delivery_end"
                if r["cycle"] <= cycle < self.lifecycle[end_name][q]["cycle"] and r["base"] <= address < r["base"] + r["bytes"]:
                    owners.append((q, address - r["base"]))
        require(len(owners) <= 1, f"overlapping live {kind} owner/generation at {address:#x}")
        return owners

    def first(self, tx, e):
        if tx["opcode"] == 4:
            require(inside(tx["address"], tx["bytes"], REGIONS), "Get outside finite registered regions")
            return
        address = tx["address"]
        if inside(address, tx["bytes"], ["c1"]):
            owners = [p for p in self.panels.values() if p["cycle"] <= e["cycle"] and
                      self.panel_acks[p["panel"]] < sum(b-a for a,b in p["expected_puts"]) and
                      any(a <= address and address + tx["bytes"] <= b for a,b in p["expected_puts"])]
            require(len(owners) == 1, "C1 Put does not have exactly one live static panel")
            tx["panel"] = owners[0]["panel"]
            tx["kind"] = "c1"
        else:
            require(inside(address, tx["bytes"], ["a2", "c2"]), "Put outside finite output regions")
            tx["kind"] = "a2" if inside(address, tx["bytes"], ["a2"]) else "c2"
        tx["history"] = []

    def write(self, tx, address, value, cycle):
        kind = tx["kind"]
        if kind == "c1":
            panel = self.panels[tx["panel"]]
            require(any(a <= address < b for a,b in panel["expected_puts"]), "C1 masked byte outside panel plan")
            offset = 0
            for begin,end in panel["expected_puts"]:
                if begin <= address < end:
                    index = offset + address - begin
                    require(not self.panel_seen[panel["panel"]][index], "duplicate physical C1 panel byte")
                    self.panel_seen[panel["panel"]][index] = 1
                    break
                offset += end - begin
            self.panel_bytes[panel["panel"]] += 1
        owners = self.owners(kind, address, cycle)
        require(owners or kind == "c1", f"{kind} byte lacks live consumer owner")
        for q, offset in owners:
            if kind == "c1":
                require(q in panel["effective_queries"], "C1 output owner absent from panel snapshot")
                fragment = next(f for f in self.query[q]["fragments"] if f["address"] <= address < f["address"]+f["length"])
                require((fragment["buffer"], fragment["buffer_generation"]) == (panel["buffer"], panel["generation"]),
                        "C1 query fragment generation differs from producing panel")
            require(not self.seen[kind][q][offset], f"duplicate historical {kind} byte q{q}:{offset}")
            require(value == self.gold[kind][q * ROW_BYTES[kind] + offset],
                    f"historical {kind} differs from independent golden q{q}:{offset}")
            self.data[kind][q][offset], self.seen[kind][q][offset] = value, 1
            tx["history"].append((kind, q, offset))

    def ack(self, tx, e):
        if tx["kind"] == "c1":
            self.panel_acks[tx["panel"]] += tx["bytes"]
            self.panel_last_ack[tx["panel"]] = e["cycle"]
        for kind, q, offset in tx["history"]:
            require(not self.acked[kind][q][offset], "duplicate historical Ack byte")
            self.acked[kind][q][offset] = 1
            self.last_ack[kind][q] = e["cycle"]
            self.last_ack_stage[kind][q] = tx["stage"]

    def finish(self, run):
        report = {}
        for kind, width in ROW_BYTES.items():
            require(all(all(row) for row in self.seen[kind]) and all(all(row) for row in self.acked[kind]),
                    f"missing historical {kind} byte/Ack coverage")
            captured = b"".join(self.data[kind])
            require(captured == self.gold[kind], f"all-query {kind} capture differs")
            optional = run / ("query_outputs.bin" if kind == "c1" else "query_" + kind + "_outputs.bin")
            if optional.is_file():
                require(read_bytes(optional) == captured, f"runtime historical {kind} capture differs from raw audit")
            report[kind] = {"bytes": self.count * width, "sha256": hashlib.sha256(captured).hexdigest(),
                            "exactly_once_value_and_ack": True}
        for pid, panel in self.panels.items():
            expected = sum(b-a for a,b in panel["expected_puts"])
            require(self.panel_bytes[pid] == self.panel_acks[pid] == expected,
                    f"physical panel {pid} payload/Ack coverage differs")
            require(all(self.panel_seen[pid]), "physical panel has uncovered static bytes")
        return report


class StageReads:
    """Finite exact stage inputs; coverage uses compact arrays, not address sets."""
    def __init__(self, controller, history, starts, config):
        self.records, self.covered, self.payload = {}, {}, Counter()
        cohorts = unique_by(controller, "cohort_start", "cohort")
        allowed_names = {"admit_inputs", "c1_group", "consumer_reload", "consumer_a2", "consumer_l2", "restore_inputs", "resume_c1_config"}
        for sid, stage in starts.items():
            name, cycle = stage["name"], stage["cycle"]
            require(name in allowed_names, "unregistered device stage kind")
            record = {"name": name, "ranges": []}
            if name in ("admit_inputs", "restore_inputs"):
                event_name = "input_load_start" if name == "admit_inputs" else "restore_start"
                matches = [e for e in controller if e["event"] == event_name and e["cycle"] == cycle]
                require(len(matches) == 1, "input/restore stage lacks exact live request plan")
                record["queries"] = matches[0]["queries"]
                require(matches[0]["live_inputs"] == len(record["queries"]) and record["queries"], "input stage row shape")
                record["ranges"] = [(REGIONS["a1"][0] + q * 896, 896) for q in record["queries"]]
                expected_write = 0
            elif name == "resume_c1_config":
                matches = [e for e in controller if e["event"] == "resume_c1_config_start" and e["cycle"] == cycle]
                require(len(matches) == 1 and matches[0]["queries"] and
                        len(matches[0]["queries"]) == matches[0]["live_inputs"], "missing retained A1 config plan")
                record["queries"] = matches[0]["queries"]
                expected_write = 0
            elif name == "c1_group":
                matches = [e for e in controller if e["event"] == "c1_group_plan" and e["cycle"] == cycle]
                require(len(matches) == 1, "C1 stage lacks exact group plan")
                plan = matches[0]
                rows, p, count = plan["rows"], plan["physical_panel"], plan["panel_count"]
                require(1 <= rows <= 16 and 1 <= count <= 2 and 0 <= p < p+count <= 14 and
                        len(plan["panels"]) == count and plan["tile_j"] == config["tile_j"], "C1 finite group shape")
                require(rows == 1 + max(history.query[q]["slot"] for q in plan["queries"]), "C1 physical activity rows differ")
                live_ids = [q for q in history.query if history.bind[q]["cycle"] <= cycle and
                            not any(e["event"] == "input_release" and e["query"] == q and e["cycle"] <= cycle for e in controller)]
                live_ids.sort(key=lambda q: history.query[q]["slot"])
                require(plan["queries"] == live_ids, "C1 owner snapshot differs from finite live input slots")
                require(config["tile_j"] != 8 or count == 2, "J8 did not reserve two N64 credits")
                for offset,pid in enumerate(plan["panels"]):
                    panel = history.panels[pid]
                    require(panel["physical_panel"] == p+offset and panel["effective_queries"] == plan["queries"] and
                            len(panel["expected_puts"]) == rows and all(b-a == 256 for a,b in panel["expected_puts"]),
                            "physical C1 panel differs from bounded group plan")
                record.update(plan=plan, weight_begin=p*64, weight_width=count*64)
                # A closed first macro performs its own compact A load.
                if config["policy"] == "closed" and p == 0:
                    record["ranges"] = [(REGIONS["a1"][0]+q*896,896) for q in plan["queries"]]
                expected_write = rows * count * 256
            else:
                earlier = [e for e in cohorts.values() if e["cycle"] <= cycle]
                require(earlier, "consumer stage before cohort allocation")
                cohort = max(earlier, key=lambda e: e["cycle"])
                ids = cohort["queries"]
                require(cohort["rows"] == len(ids) and 1 <= len(ids) <= 16, "consumer cohort shape")
                record.update(cohort=cohort["cohort"], queries=ids)
                if name == "consumer_reload":
                    record["ranges"] = [(f["address"],f["length"]) for q in ids for f in history.query[q]["fragments"]]
                    expected_write = 0
                elif name == "consumer_l2":
                    record["ranges"] = [(history.register["a2"][q]["base"],896) for q in ids] + [REGIONS["w2"]]
                    expected_write = len(ids) * 512
                else:
                    expected_write = len(ids) * 896
            require(stage["expected_write_bytes"] == expected_write, "stage E does not match independent dimensions")
            self.records[sid] = record
            self.covered[sid] = [bytearray(length) for _,length in record["ranges"]]
            if name == "c1_group":
                self.covered[sid].append(bytearray(896 * record["weight_width"]))

    def first(self, tx):
        sid, address, length = tx["stage"], tx["address"], tx["bytes"]
        record = self.records[sid]
        if tx["opcode"] != 4:
            expected_kind = {"c1_group": "c1", "consumer_a2": "a2", "consumer_l2": "c2"}.get(record["name"])
            require(expected_kind is not None and inside(address,length,[expected_kind]), "stage writes unexpected output kind")
            return
        self.payload[sid] += length
        if record["name"] == "c1_group" and inside(address,length,["w1"]):
            rel = address - REGIONS["w1"][0]
            row, column = divmod(rel,896)
            begin, width = record["weight_begin"], record["weight_width"]
            require(0 <= row < 896 and begin <= column and column+length <= begin+width,
                    "C1 Get outside selected W1 N64 panels")
            offset = row * width + column - begin
            require(not any(self.covered[sid][-1][offset:offset+length]), "duplicate W1 dependency Get")
            self.covered[sid][-1][offset:offset+length] = bytes([1]) * length
            return
        for index,(begin,width) in enumerate(record["ranges"]):
            if begin <= address and address+length <= begin+width:
                offset = address - begin
                if record["name"] != "consumer_l2" or (begin,width) != REGIONS["w2"]:
                    require(not any(self.covered[sid][index][offset:offset+length]), "duplicate stage dependency Get")
                self.covered[sid][index][offset:offset+length] = bytes([1]) * length
                return
        raise AuditError(f"Get outside exact stage input/dependency: stage{sid} {record['name']} {address:#x}+{length}")

    def finish(self):
        for sid, arrays in self.covered.items():
            require(all(all(array) for array in arrays), f"missing exact stage input Get coverage: stage{sid}")
        return {"stages": len(self.records), "all_expected_input_bytes_read": True,
                "get_payload_bytes": sum(self.payload.values())}


def audit(input_dir, run, count):
    arrivals, gold, initial = verify_inputs(input_dir, count)
    require(len(initial) == MEMORY_BYTES, "initial memory size")
    controller = list(events(artifact(run, "driver_events.jsonl")))
    config, result = load(artifact(run, "runtime_config.json")), load(artifact(run, "result.json"))
    require(result.get("status") not in ("failed", "error") and result["cpu_instantiated"] is False,
            "run failed or instantiated CPU")
    require(config["query_count"] == count and config["n"] == config["k"] == 896 and
            config["poll_cycles"] == 64 and config["decision_cycles"] == 16 and
            config["issue_cycles"] == 1 and config["sink_cycles"] == 64, "run shape/fee differs")
    require(config["policy"] in ("on", "off", "closed"), "unregistered admission policy")
    for e in controller:
        require(not e.get("used_for_control", False), "observer feedback marked as controller input")
        require(not any(x in str(e.get("control_source", "")).lower() for x in ("observer", "golden", "tl_parser")),
                "forbidden controller observation source")
    operations, cpu = cpu_audit(controller)
    history = History(controller, count, gold)
    starts = unique_by(controller, "stage_start", "stage")
    all_accepted = unique_by(controller, "stage_all_accepted", "stage")
    seals = unique_by(controller, "sealed_group_complete", "stage")
    idles = unique_by(controller, "stage_idle_sealed", "stage")
    require(starts and set(starts) == set(all_accepted) == set(seals) == set(idles),
            "missing real counter/busy stage lifecycle")
    stage_order = sorted(starts, key=lambda s: starts[s]["cycle"])
    start_cycles = [starts[s]["cycle"] for s in stage_order]
    for a,b in zip(stage_order, stage_order[1:]):
        require(idles[a]["cycle"] <= starts[b]["cycle"], "device stages overlap before actual busy gate")
    stage_reads = StageReads(controller, history, starts, config)
    stage_ack, stage_payload, stage_last_d = Counter(), Counter(), Counter()
    busy_needed = {e["sample_cycle"] for e in controller if e["event"] in ("status_poll_observe", "stage_idle_sealed")}
    busy, live_at_busy, ack_at_busy = {}, {}, {}
    cumulative_ack = 0
    command_wire = WireCommands()
    ack_at_response, captures_ack = {}, []
    busy_first = busy_last = None
    busy_count = 0
    def first(tx, e):
        index = bisect_right(start_cycles, e["cycle"]) - 1
        require(index >= 0, "DMA before any sealed stage")
        sid = stage_order[index]
        require(e["cycle"] <= idles[sid]["cycle"], "DMA after stage idle fence")
        tx["stage"] = sid
        stage_reads.first(tx)
        history.first(tx, e)
    def write(tx, address, value, cycle):
        stage_payload[tx["stage"]] += 1
        history.write(tx, address, value, cycle)
    def ack(tx, e):
        nonlocal cumulative_ack
        cumulative_ack += tx["bytes"]
        stage_ack[tx["stage"]] += tx["bytes"]
        history.ack(tx, e)
    raw = RawAudit(initial=initial, memory_base=BASE, on_first=first, on_write=write,
                   on_ack=ack, retain_transactions=False)
    for e in events(artifact(run, "engine_events.jsonl")):
        kind, cycle = e["event"], e["cycle"]
        command_wire.consume(e)
        if kind == "tl_d":
            tx = raw.live.get(e["source"])
            require(tx is not None, "D has no independently reconstructed source")
            stage_last_d[tx["stage"]] = cycle
        if kind == "device_status_sample":
            require(isinstance(e["busy"], bool) and (busy_last is None or cycle == busy_last + 1),
                    "actual busy stream omitted/repeated a cycle")
            busy_first = cycle if busy_first is None else busy_first
            busy_last, busy_count = cycle, busy_count + 1
            if cycle in busy_needed:
                busy[cycle], live_at_busy[cycle], ack_at_busy[cycle] = e["busy"], len(raw.live), cumulative_ack
        if kind == "rocc_response":
            ack_at_response[e["tag"]] = cumulative_ack
        if kind == "observer_counter_capture":
            captures_ack.append(cumulative_ack)
        raw.consume(e)
    wire = raw.finish()
    commands = command_wire.finish()
    require(busy_count > 0 and set(busy) == busy_needed, "missing requested actual busy sample")
    require(read_bytes(artifact(run, "actual_memory.bin")) == bytes(raw.memory), "complete16MiB wire replay vs dump differs")
    for name in ("a1", "w1", "w2"):
        address,length = REGIONS[name]
        off = address - BASE
        require(raw.memory[off:off+length] == initial[off:off+length], f"immutable region {name} modified")
    require([(e["query"], e["cycle"]) for e in raw.arrivals] == [(e["query"], e["cycle"]) for e in arrivals],
            "raw independent arrival chronology differs")
    command_plans = unique_by(controller, "command_plan", "tag")
    observations = unique_by(controller, "command_accept_observed", "tag")
    responses = unique_by(controller, "response_observed", "tag")
    require(set(command_plans) == set(observations) == set(command_wire.accepts), "planned/accepted command coverage")
    require(set(responses) == set(command_wire.fires), "real response observation coverage")
    issue_ops = {e["tag"]: e for e in operations if e["kind"] == "issue"}
    require(len(issue_ops) == sum(e["kind"] == "issue" for e in operations) and set(issue_ops) == set(command_plans),
            "issue fee coverage")
    for tag, accepted in command_wire.accepts.items():
        plan, observed = command_plans[tag], observations[tag]
        require(command_wire.payload(plan) == command_wire.payload(accepted), "accepted ISA differs from public plan")
        require(issue_ops[tag]["end"] == command_wire.tries[tag]["cycle"], "command first presentation precedes/omits paid issue")
        require(observed["accepted_cycle"] == accepted["cycle"] and observed["cycle"] == accepted["cycle"] + 1,
                "command accepted observation violates next-cycle causality")
    for tag, fire in command_wire.fires.items():
        observed = responses[tag]
        require(observed["cycle"] == fire["cycle"] + 1 and observed["response_cycle"] == fire["cycle"] and
                (observed["rd"], observed["data"]) == (fire["rd"], fire["data"]), "controller response differs/too early")
        require(any(o["kind"] == "response_decision" and o["start"] == observed["cycle"] for o in operations),
                "response lacks paid decision16")
        require(fire["data"] <= ack_at_response[tag], "counter response exceeds actual accepted transfer Ack bytes")
    counter_accepts = [e for e in command_wire.accepts.values() if e["funct"] == 126]
    require(len(command_wire.captures) == len(counter_accepts), "missing independent real-counter capture evidence")
    previous_value = 0
    for i,(capture, accepted, acknowledged) in enumerate(zip(command_wire.captures, counter_accepts, captures_ack)):
        fire = command_wire.fires[accepted["tag"]]
        require(capture["cycle"] >= accepted["cycle"] and capture["cycle"] < fire["cycle"] and
                capture["rd"] == 1 and capture["source_data"] == capture["response_data"] == fire["data"],
                "counter source/capture/real response disagree")
        if i >= 2:
            require(previous_value <= fire["data"] <= acknowledged, "real counter regressed or preceded actual Ack")
            previous_value = fire["data"]
    baseline = 0
    for sid in stage_order:
        start, done, seal, idle = starts[sid], all_accepted[sid], seals[sid], idles[sid]
        expected = start["expected_write_bytes"]
        require(start["baseline"] == baseline and start["target"] == ((baseline + expected) & 0xffffffff),
                "stage target not derived from previous sealed baseline")
        stage_commands = [e for e in command_plans.values() if e.get("stage") == sid and e["funct"] != 126]
        require(stage_commands and done["command_count"] == len(stage_commands), "stage command count/coverage")
        last_accept = max(command_wire.accepts[e["tag"]]["cycle"] for e in stage_commands)
        require(done["last_accepted_cycle"] == last_accept and done["cycle"] == last_accept + 1,
                "stage all-accepted notification differs")
        counter_reads = [e for e in command_plans.values() if e.get("stage") == sid and e["funct"] == 126]
        require(counter_reads, "stage lacks real completion poll")
        for read in counter_reads:
            tag = read["tag"]
            delta = (command_wire.fires[tag]["data"] - baseline) & 0xffffffff
            require(delta <= expected and command_wire.tries[tag]["cycle"] > last_accept,
                    "completion read overshot or precedes all commands")
            require(any(o["kind"] == "counter_poll" and o["end"] == issue_ops[tag]["start"] for o in operations),
                    "counter read lacks paid poll64")
        final = command_wire.fires[counter_reads[-1]["tag"]]
        require(seal["response_cycle"] == final["cycle"] and seal["cycle"] == final["cycle"] + 17 and
                seal["baseline"] == baseline and seal["delta"] == expected and
                seal["target"] == final["data"] == start["target"], "counter equality seal differs from actual response/paid decision")
        require(stage_ack[sid] == stage_payload[sid] == expected and stage_last_d[sid] <= idle["sample_cycle"],
                "physical stage bytes/Acks/drain differ from static E")
        status = [e for e in controller if e["event"] == "status_poll_observe" and e["stage"] == sid]
        require(status and status[-1]["busy"] is False, "stage lacks terminal actual-busy false observation")
        for e in status:
            sample = e["sample_cycle"]
            require(sample == e["cycle"] - 1 and sample > last_accept and e["busy"] is busy[sample],
                    "actual busy observation value/causality differs")
            require(any(o["kind"] == "status_poll" and o["end"] == e["cycle"] for o in operations) and
                    any(o["kind"] == "status_decision" and o["start"] == e["cycle"] for o in operations),
                    "status observation lacks paid poll64/decision16")
        sample = idle["sample_cycle"]
        require(idle["cycle"] == sample + 17 and sample == status[-1]["sample_cycle"] and
                idle["last_accepted_cycle"] == last_accept and idle["counter_value"] == final["data"] and
                busy[sample] is False and live_at_busy[sample] == 0 and ack_at_busy[sample] == final["data"],
                "resource fence precedes actual busy false/full source drain/counter equality")
        baseline = final["data"]
    require(baseline == cumulative_ack, "final counter not equal complete raw Ack transfer bytes")
    histories = history.finish(run)
    reads = stage_reads.finish()
    resources = check_resources(controller, history, starts, idles, seals, arrivals, operations, config)
    query_records = check_query_records(run, history, arrivals)
    require(any(e["event"] == "engine_drained" for e in controller), "missing controller drain")
    require(raw.drained_cycle >= max(e["cycle"] for e in idles.values()), "engine drained before last actual busy fence")
    for field, value in (("rocc_commands", wire["accepted_commands"]), ("tl_get_payload_bytes", wire["get_payload_bytes"]),
                         ("tl_put_transfer_bytes", cumulative_ack), ("tl_put_masked_bytes", wire["put_payload_bytes"])):
        require(result[field] == value, f"engine aggregate differs: {field}")
    return {"status": "pass", "scope": "independent online functional/protocol diagnostic; no held-out performance inference",
            "queries": count, "wire": wire, "commands": commands, "historical_outputs": histories,
            "stage_input_dependencies": reads,
            "full_memory_bytes_equal": MEMORY_BYTES, "actual_busy_samples": busy_count,
            "sealed_stages": len(starts), "cpu_modeled_cycles": cpu, "resources": resources,
            "query_results": query_records, "hashes": {"checker": sha(__file__), "fixture_manifest": FIXTURE_SHA,
              "engine_events": sha(artifact(run, "engine_events.jsonl")), "driver_events": sha(artifact(run, "driver_events.jsonl")),
              "actual_memory": sha(artifact(run, "actual_memory.bin"))}}


def check_resources(controller, history, starts, idles, seals, arrivals, operations, config):
    """Validate lifetimes and credits using public plans plus wire-derived completion."""
    allocations, releases = {}, {}
    slot_live, slot_generation = {}, Counter()
    max_live = 0
    require([e["query"] for e in controller if e["event"] == "query_bind"] == list(range(history.count)),
            "admission violates FCFS arrival order")
    metadata_counts = Counter(e["category"] for e in controller if e["event"] == "metadata_op")
    cohorts = unique_by(controller, "cohort_start", "cohort")
    cohort_releases = unique_by(controller, "cohort_release", "cohort")
    fragment_releases = unique_by(controller, "query_fragment_release", "query")
    panel_releases = unique_by(controller, "panel_release", "panel")
    require(set(fragment_releases) == set(history.query) and set(panel_releases) == set(history.panels) and
            set(cohorts) == set(cohort_releases), "missing fragment/panel/cohort release lifecycle")
    for category, expected in (("query_bind",history.count), ("query_c1_ready",history.count),
            ("input_release",history.count), ("query_fragment_release",history.count),
            ("panel_snapshot",len(history.panels)), ("cohort_map",len(cohorts)),
            ("cohort_release",len(cohorts)), ("restore_plan",len(cohorts))):
        require(metadata_counts[category] == expected, f"metadata payment missing/extra for actual operation {category}")
    for e in controller:
        name = e["event"]
        if name == "query_bind":
            q, slot = e["query"], e["slot"]
            require(slot not in slot_live and 0 <= slot < 16 and e["cycle"] >= arrivals[q]["cycle"],
                    "input slot reuse/early admission")
            slot_generation[slot] += 1
            require(e["slot_generation"] == slot_generation[slot], "input slot generation does not advance")
            slot_live[slot] = q
            max_live = max(max_live, len(slot_live))
            plan = history.query[q]
            require(plan["slot_generation"] == e["slot_generation"] and plan["slot"] == slot,
                    "query plan/bind generation differs")
            if config["policy"] == "off":
                require(plan["start_panel"] % 14 == 0, "OFF admitted away from scan origin")
        elif name == "input_release":
            q = e["query"]
            slot = history.query[q]["slot"]
            require(slot_live.get(slot) == q and history.last_ack["c1"][q] < e["cycle"], "input release before C1 reads/output completion")
            sid = history.last_ack_stage["c1"][q]
            require(idles[sid]["cycle"] <= history.lifecycle["c1_notified"][q]["cycle"] <= e["cycle"],
                    "input released before its own C1 stage busy fence/notification")
            del slot_live[slot]
        elif name == "buffer_alloc":
            key = (e["buffer"], e["generation"])
            require(key not in allocations, "duplicate C1 generation allocation")
            allocations[key] = e
        elif name == "buffer_release":
            key = (e["buffer"], e["generation"])
            require(key in allocations and key not in releases, "unallocated/duplicate C1 generation release")
            releases[key] = e
    require(not slot_live, "unreleased input slot at drain")
    require(allocations and set(allocations) == set(releases), "unreleased C1 epoch generations")
    for key, allocation in allocations.items():
        end = releases[key]["cycle"]
        require(allocation["cycle"] <= end, "negative C1 buffer lease")
        for q, plan in history.query.items():
            if any((f["buffer"], f["buffer_generation"]) == key for f in plan["fragments"]):
                require(allocation["cycle"] <= plan["cycle"] and end >= fragment_releases[q]["cycle"],
                        "C1 fragment reference outlives/recedes its generation lease")
        require(inside(allocation["address"], allocation["length"], ["c1"]), "C1 allocation outside finite epochs")
    by_buffer = defaultdict(list)
    for key, allocation in allocations.items():
        by_buffer[key[0]].append((key, allocation))
    for chain in by_buffer.values():
        chain.sort(key=lambda pair: pair[1]["cycle"])
        for (old, a),(new, b) in zip(chain, chain[1:]):
            require(b["cycle"] >= releases[old]["cycle"] and new[1] == old[1]+1,
                    "C1 generation overwritten before fragment release")
    for key, a in allocations.items():
        for other, b in allocations.items():
            if key != other and a["cycle"] < b["cycle"] < releases[key]["cycle"]:
                require(max(a["address"],b["address"]) >= min(a["address"]+a["length"],b["address"]+b["length"]),
                        "physical C1 epoch allocated with an unreleased generation")
    # Independent physical credit occupancy: reservation until the containing stage's paid idle fence.
    reservations = []
    stage_order = sorted(starts, key=lambda s: starts[s]["cycle"])
    start_cycles = [starts[s]["cycle"] for s in stage_order]
    for panel in history.panels.values():
        acknowledged = history.panel_last_ack[panel["panel"]]
        index = bisect_right(start_cycles, acknowledged) - 1
        sid = stage_order[index]
        require(panel["reserved_credit"] == 1 and 0 <= panel["physical_panel"] < 14,
                "panel credit/physical identity outside finite bounds")
        released = panel_releases[panel["panel"]]["cycle"]
        require(released >= idles[sid]["cycle"], "panel credit released before actual stage busy fence")
        reservations.append((panel["cycle"], 1))
        reservations.append((released, -1))
        effective = panel["effective_queries"]
        require(len(set(effective)) == len(effective) and all(q in history.query for q in effective), "panel query identity")
    occupancy = max_credit = 0
    for cycle, delta in sorted(reservations, key=lambda v: (v[0], v[1])):
        occupancy += delta
        require(0 <= occupancy <= 2, "more than two finite N64 panel credits live")
        max_credit = max(max_credit, occupancy)
    require(occupancy == 0, "credits not drained")
    # Every query must receive fourteen distinct logical N64 panels exactly once.
    for q, plan in history.query.items():
        physical = [p["physical_panel"] for p in history.panels.values() if q in p["effective_queries"]]
        require(sorted(physical) == list(range(14)), f"query q{q} panel coverage/repetition")
        stamps = [history.bind[q]["cycle"]] + [history.lifecycle[k][q]["cycle"] for k in
                   ("c1_notified", "consumer_start", "a2_sealed", "l2_start", "c2_notified", "delivery_end", "query_release")]
        require(stamps == sorted(stamps), f"query q{q} lifecycle order")
        for kind, notice in (("c1", "c1_notified"), ("a2", "a2_sealed"), ("c2", "c2_notified")):
            cycle = history.lifecycle[notice][q]["cycle"]
            sid = history.last_ack_stage[kind][q]
            require(starts[sid]["name"] == {"c1":"c1_group","a2":"consumer_a2","c2":"consumer_l2"}[kind] and
                    history.last_ack[kind][q] < seals[sid]["cycle"] <= cycle,
                    f"{notice} lacks its own paid real-counter stage completion")
            if kind in ("c1", "a2"):
                require(cycle >= idles[sid]["cycle"], f"{notice} precedes its own actual busy fence")
            else:
                require(cycle == seals[sid]["cycle"], "C2 notification differs from paid equality decision")
        a2_notice = history.lifecycle["a2_sealed"][q]["cycle"]
        a2_fence = idles[history.last_ack_stage["a2"][q]]["cycle"]
        require(fragment_releases[q]["cycle"] >= max(a2_fence, a2_notice),
                "C1 reference released before requantization actual busy fence")
        delivered = history.lifecycle["delivery_end"][q]["cycle"]
        require(any(o["kind"] in ("sink", "delivery") and o["end"] == delivered and
                    o.get("queries") == [q] for o in operations), "delivery lacks actual paid64 interval")
    for kind in ("a2", "c2"):
        registrations = list(history.register[kind].values())
        for old in registrations:
            q = old["query"]
            fence = idles[history.last_ack_stage["c2"][q]]["cycle"]
            safe = max(fence, history.lifecycle["delivery_end"][q]["cycle"] if kind == "c2" else 0)
            for new in registrations:
                if new["cycle"] > old["cycle"] and max(old["base"], new["base"]) < min(old["base"]+old["bytes"],new["base"]+new["bytes"]):
                    require(new["cycle"] >= safe and new["generation"] > old["generation"],
                            f"{kind} generation reused before L2/delivery busy fence")
    for cid, cohort in cohorts.items():
        ids, released = cohort["queries"], cohort_releases[cid]
        require(1 <= cohort["rows"] == len(ids) <= 16 and ids == sorted(set(ids)) and
                released["queries"] == ids and released["generation"] == cohort["generation"], "cohort membership/generation changed")
        for row,q in enumerate(ids):
            require(history.lifecycle["consumer_start"][q]["cohort"] == cid and
                    history.lifecycle["consumer_start"][q]["row"] == row and
                    history.lifecycle["l2_start"][q]["cohort"] == cid and
                    history.register["a2"][q]["base"] == REGIONS["a2"][0]+row*896 and
                    history.register["c2"][q]["base"] == REGIONS["c2"][0]+row*512,
                    "cohort physical packed row mapping differs")
            require(released["cycle"] == history.lifecycle["query_release"][q]["cycle"] >= history.lifecycle["delivery_end"][q]["cycle"],
                    "cohort released before ordered deliveries")
    check_admission(controller, history, arrivals, config, operations, starts, idles)
    return {"max_input_slots": max_live, "max_panel_credits": max_credit,
            "c1_generation_allocations": len(allocations), "c1_buffer_reuse_exercised": len(allocations) > len({k[0] for k in allocations}),
            "slot_reuse_exercised": max(slot_generation.values(), default=0) > 1,
            "rotated_admission_exercised": any(p["start_panel"] % 14 for p in history.query.values()),
            "consumer_cohorts": len({e["cohort"] for e in history.register["a2"].values()})}


def check_admission(controller, history, arrivals, config, operations, starts, idles):
    observed, bound, pending_batch = set(), set(), None
    ready, consuming = set(), set()
    batch_ordinal = 0
    for e in controller:
        name = e["event"]
        if name == "query_observe":
            q = e["query"]
            require(q not in observed and e["cycle"] > arrivals[q]["cycle"] and
                    any(o["kind"] == "poll" and o["end"] == e["cycle"] for o in operations),
                    "arrival observed without fresh paid scheduler poll")
            observed.add(q)
        elif name == "batch_decision":
            require(config["policy"] == "closed" and pending_batch is None, "batch decision outside closed scheduler")
            backlog = sorted(observed-bound)
            require(backlog and e["backlog"] == len(backlog) and
                    e["oldest_arrival"] == arrivals[backlog[0]]["cycle"] and
                    e["batch_target"] == config["batch_target"] and e["timeout_cycles"] == config["timeout_cycles"],
                    "closed decision backlog/timeout parameters differ from independent chronology")
            launch = len(backlog) >= config["batch_target"] or e["cycle"] >= arrivals[backlog[0]]["cycle"] + config["timeout_cycles"]
            require(e["launch"] is launch, "closed threshold/timeout launch differs")
            if launch:
                pending_batch = backlog[:16]
        elif name == "query_bind":
            q = e["query"]
            require(q in observed and q not in bound and q == min(observed-bound), "bind violates observed FCFS backlog")
            if config["policy"] == "closed":
                require(pending_batch is not None and q in pending_batch and
                        history.query[q]["start_panel"] == batch_ordinal * 14, "closed query admitted outside launched compact batch")
            else:
                scan = sum(next(g["panel_count"] for g in controller if g["event"] == "c1_group_plan" and g["cycle"] == s["cycle"])
                           for sid,s in starts.items() if s["name"] == "c1_group" and idles[sid]["cycle"] <= e["cycle"])
                require(history.query[q]["start_panel"] == scan, "ON/OFF descriptor does not start at actual scan boundary")
            require(not any(s["cycle"] < e["cycle"] < idles[sid]["cycle"] for sid,s in starts.items()),
                    "query admitted during an unsealed device stage")
            bound.add(q)
        elif name == "c1_notified":
            ready.add(e["query"])
        elif name == "cohort_start":
            require(e["queries"] == sorted(ready-consuming), "consumer cohort omits known-ready request or waits for unready batch")
            consuming.update(e["queries"])
        elif name == "c1_group_plan":
            require(not (ready-consuming), "L1 work issued while a completed query was waiting for immediate consumer")
        elif name == "batch_start":
            require(config["policy"] == "closed" and e["queries"] == pending_batch and e["ordinal"] == batch_ordinal and
                    e["rows"] == len(pending_batch) and e["first_query"] == pending_batch[0], "closed compact batch membership differs")
            pending_batch = None
            batch_ordinal += 1
    require(observed == bound == set(history.query) and pending_batch is None, "unobserved/unadmitted request at drain")


def check_query_records(run, history, arrivals):
    records = load(artifact(run, "query_results.json"))
    if isinstance(records, dict):
        records = records["queries"]
    require(isinstance(records, list) and len(records) == history.count, "query result cardinality")
    records = sorted(records, key=lambda r: r["query"])
    require([r["query"] for r in records] == list(range(history.count)), "query result identity")
    for q, record in enumerate(records):
        require(record["arrival"] == arrivals[q]["cycle"] and record["admitted"] == history.bind[q]["cycle"],
                "query result arrival/admission differs from event history")
        for key in ("c1_notified", "consumer_start", "a2_sealed", "c2_notified", "delivery_end"):
            require(record[key] == history.lifecycle[key][q]["cycle"], f"query result {key} differs")
        require(record["panel_count"] == 14, "query result panel count")
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--queries", required=True, type=int, choices=(4, 32))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = audit(args.input_dir, args.run_dir, args.queries)
    except (AuditError, AssertionError, KeyError, ValueError, TypeError, OSError) as error:
        report = {"status": "fail", "detail": f"{type(error).__name__}: {error}",
                  "queries": args.queries, "checker_sha256": sha(__file__)}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report["status"] != "pass"


if __name__ == "__main__":
    sys.exit(main())
