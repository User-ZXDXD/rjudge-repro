# G-Safeguard复现适配与重跑边界

工作台默认运行只需要`requirements-lab.txt`，不需要Torch、模型权重或API Key。以下是可选研究环境，与页面的历史结果核算分开。

## 对应代码

| 环节 | 文件 | 实际职责 |
| --- | --- | --- |
| 回复解析 | `benchmarks/gsafeguard/parsing_adapter.py` | 提取答案、公开理由、检查格式；不把格式有效当成答案正确 |
| 数据与通信 | `benchmarks/gsafeguard/core_runner.py` | 按题划分32/8/16，固定配对角色与错误目标，记录逐轮通信 |
| 图模型训练 | `benchmarks/gsafeguard/graph_training.py` | MiniLM特征、源→目标方向、官方MyGAT、验证集选权重 |
| 独立指标 | `benchmarks/gsafeguard/independent_metrics.py` | 固定分母、正常节点指标、按题配对bootstrap |
| 原始运行核验 | `benchmarks/gsafeguard/audit_completed_run.py` | 需要本地完整运行文件和权重，不能只凭发布派生字段完成全部审计 |

这里移植的是个人复现适配器，官方MyGAT代码另从上游获取；官方仓库未随本产品重新打包。

## 固定来源与依赖

- 官方代码：[wslong20/G-safeguard](https://github.com/wslong20/G-safeguard)，历史固定提交`890c99f1cbc864e9ff0c85859619a14f42bc9cab`。
- 数据：该提交的`PI/datasets/MMLU/test/computer_security_test.csv`，检查100行、题目去重、按question_id隔离。
- 嵌入：`sentence-transformers/all-MiniLM-L6-v2`，revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`。文件哈希见`benchmarks/gsafeguard/embedding_source_manifest.json`。
- 本机核验环境：Python3.12、Torch2.5.1+cu121、torch-geometric2.6.1、NumPy2.5.3、scikit-learn1.9.1、sentence-transformers6.1.0、OpenAI SDK3.24.0。是已存在环境的实际版本记录，不代表新环境安装已逐一验证；按设备选择CPU/CUDA构建，不覆盖现有环境。

把上游仓库放在项目`vendor/G-Safeguard`，模型放在`benchmarks/gsafeguard/embedding_model`；也可用环境变量指向已有副本：

```powershell
git clone https://github.com/wslong20/G-safeguard.git vendor/G-Safeguard
git -C vendor/G-Safeguard checkout 890c99f1cbc864e9ff0c85859619a14f42bc9cab
# 模型请从上述固定revision下载，保留1_Pooling子目录，再按manifest核对哈希。
# 如使用已有目录，设置这两个变量；不填写时使用上面的相对默认位置。
# $env:GSAFEGUARD_UPSTREAM='你的上游仓库目录'
# $env:GSAFEGUARD_EMBEDDING='你的MiniLM模型目录'
python benchmarks/gsafeguard/core_runner.py --check
```

本轮已用已有上游/权重验证`--check`：100行、32/8/16拆分、官方提交与干净工作树、协议和必要导入通过；API调用0次。这不等于完整付费流程已在新目录再次执行。

## 历史设置与新运行要分开

历史发布结果来自16道测试题、四个条件。旧采集过程曾包含格式修复与续跑，不能把当前最终输出约束说成历史所有样本都从头采用了同一个提示词版本。发布数据用于重算历史结果；若研究提示词版本影响，需要重新固定采集协议并独立比较。

适配器`--all`会触发新的真实API实验，默认`--check`不会。旧协议计划4352次调用，最多4608次，预算记录20元是按**2026-11-06历史单价估算**，不是平台硬扣费上限或当前报价。新运行前必须核实提供方模型、单价、输出协议与费用边界；不要将API Key提交到仓库或聊天。

没有在本轮重新运行整套付费实验，也不因为入口检查通过就声称复现了论文所有实验、所有攻击或大规模迁移结果。
