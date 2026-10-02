# results/ 说明

这里是两次已完成运行的**结果快照**，提交进仓库是为了让结论可核对。

| 目录 | 子集 | 条数 | F1 |
|---|---|---|---|
| `deepseek-flash_unintended/` | 非蓄意风险 | 150 | 88.18% |
| `deepseek-flash_injection/` | 注入攻击 | 414 | 91.32% |

每个目录下有两个文件：

- `summary.json` —— 机器可读的指标汇总（总体 + 分类别的 F1 / Recall / Specificity / 混淆矩阵）
- `analysis.md` —— 人可读的分析报告，含全部错判明细（每条都带人类标注、模型分析原文、模型结论）

运行时 `rj_runner.py` 会把原始输出写到 `out/<模型>_<子集>/`，其中
`records.jsonl` 和 `results.json` 体积较大且属于逐条原始记录，已被 `.gitignore`
排除；`analyze.py` 会从原始记录重新生成 `summary.json` 和 `analysis.md`。
所以本目录里的文件都可以用 `out/` 里的原始记录重新算出来。
