# Agent Safety Lab

![本地实际演示截图](docs/assets/mas-review.jpg)

**智能体安全评测与实验复盘工作台**：把R-Judge风险判断和G-Safeguard多智能体防护放进同一个产品入口，保留各自的任务、分母和结论边界。

本项目在原`rjudge-repro`仓库中扩展，保留其Git历史、命令行脚本、历史结果与MIT许可说明。没有把两篇论文发明成一个新防御算法，也未声称已将防护器部署到DevWeave AI。

## 四个可以操作的模块

| 模块 | 能做什么 | 不能据此声称什么 |
| --- | --- | --- |
| 证据总览 | 查看历史结果、来源哈希与处理范围 | 本次重新运行了完整模型实验 |
| 风险判断 | F1、召回、特异度、有效率、混淆矩阵与退化基线 | 单看F1就能判断部署安全 |
| MAS防护复盘 | 查看四条件、16题配对、8节点逐轮选项和防护记录 | 节点回答错误就是被感染，或连线恢复了因果链 |
| 导入与离线核算 | 输入JSON、校验数据、重算指标、导出带ID报告 | 导入结果经过了新的LLM推理 |

## 快速开始：不需要API Key

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lab.txt
python -m safety_lab
```

打开 **http://127.0.0.1:8899**。服务默认只监听本机；报告写入`.lab/runs/`。启动与核算不调用模型。没有公网认证、多租户隔离和持久化任务队列，先作为本地研究/演示工作台使用。

导入格式：`label=0`安全、`label=1`不安全，`output`为已生成的判断文本；也可提供`raw_pred=-1/0/1`。重复ID、非法标签、冲突字段会拒绝；无效模型输出保留在分母，并按官方错误策略计分。

```json
[
  {"id":"example-1","label":1,"output":"unsafe"},
  {"id":"example-2","label":0,"output":"safe"}
]
```

## 真实历史结果与范围

| 实验 | 数据范围 | 本工作台重算 |
| --- | --- | --- |
| R-Judge / unintended | 150条 | F1 88.18%，召回98.98%，特异度51.92% |
| R-Judge / injection | 414条 | F1 91.32%，召回100%，特异度82.24% |
| G-Safeguard / 攻击条件 | 16道配对测试题，每题8节点 | 末轮失败节点38/128（无防护）→44/128（有防护） |
| G-Safeguard / 正常条件 | 同一批题，另行生成正常场景 | 两臂均8/128失败节点 |

R-Judge采用历史`deepseek-flash`结果；模型、预算、数据版本与论文不同，不能解释为在受控条件下超过原论文模型。全部判不安全的基线也可得到很高F1，因此同时展示虚警与漏报。

G-Safeguard是**协议适配的小规模复现**，采用DeepSeek、384维MiniLM嵌入和官方MyGAT，不是全论文原样数值复现。本轮保留负结果：该样本上有防护的失败比例更高，没有观察到收益。16道题才是独立重复单位，128个节点不是128次独立实验；页面显示按题配对的bootstrap区间，不据此推出方法普遍无效。

发布数据只有样本ID、标注/预测、选项和有限防护字段；不重新分发原始问题、模型长回复、用户消息或密钥。来源哈希在`safety_lab/data/provenance.json`，完整原始记录保留在本地研究档案。

## 继续看哪里

| 入口 | 内容 |
| --- | --- |
| [产品说明](docs/product.md) | 为什么统一入口、如何设计指标和验收 |
| [面试讲解](docs/interview.md) | 结果解释、负结果和技术追问 |
| [验证记录](docs/validation.md) | 测试、实际页面操作和限制 |
| [复现适配说明](docs/gsafeguard.md) | 官方代码版本、依赖、配置与额外模型实验的边界 |
| [历史R-Judge说明](docs/rjudge-history.md) | 原有协议与CLI使用方式 |
| [历史复现报告](docs/reproduction_report.md) | 原仓库已发布的分析材料 |
| [第三方来源](NOTICE.md) | 论文、上游代码/数据与许可证范围 |

```powershell
python -m pytest -q
```

`rj_runner.py`等原有CLI继续保留。重新请求模型时显式设置`RJ_API_KEY`、`RJ_BASE_URL`、`RJ_MODEL`；不会借用Codex账号凭据。工作台离线核算完全不需要这些环境变量。G-Safeguard的可选代码在`benchmarks/gsafeguard/`，默认检查与实际付费运行分开。

论文：[R-Judge正式版](https://aclanthology.org/2024.findings-emnlp.79/)、[G-Safeguard官方代码](https://github.com/wslong20/G-safeguard)。本仓库的代码许可证不覆盖上游论文、数据和未重新分发的官方实现。
