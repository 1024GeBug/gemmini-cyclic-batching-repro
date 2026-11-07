DATA_STATUS: PROVIDED
STYLE_STATUS: ESTIMATED
REPRODUCIBILITY_STATUS: CODED
EXPORT_STATUS: READY
INTEGRITY_STATUS: PASS

科学信息：细粒度循环准入可以减少准入等待，但在当前轨迹中，该节省不能覆盖相对于整轮J4即时的准入后跨度增加。图的主张是端到端比较及描述性区间分解；不建立控制、恢复或消费者批次的独立因果份额。

数据仅来自`work/consumer_gate/comparison_v6/`的`summary.json`、`paired_queries.csv`、`policy_summary.csv`和`manifest.json`。脚本核对manifest输出哈希、接受状态、32请求相同到达、所有五区间的端点与加总、JSON/CSV均值以及逐请求差。

图为18×6.8 cm双面板，适合IEEE双栏整宽排版。字体8–9.1 pt；当前样式为ESTIMATED，未声称通过DAC当届官方图形规范核验。

- 面板a：横向线性堆叠均值，零起点；四策略为ON、OFF、两块J4即时、整轮J4即时。五区间使用固定颜色，窄区间不作宽度放大。
- 面板b：原始请求顺序的配对有符号棒棒糖图；纵轴单位千周期，零线清楚可见。正差用实心点、负差用空心点，不依赖颜色识别。
- 不使用置信区间、p值、p99、总体分布推断、平滑或排序。

选择理由：堆叠图展示平均等待与准入后跨度在同一最终延迟中的份额；逐请求配对图补足平均值隐藏的个体差异。两面板回答不同问题，使用各自明确标记的线性尺度。
