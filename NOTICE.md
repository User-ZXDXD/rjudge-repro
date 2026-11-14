# 来源与许可范围

本仓库由张旭东的R-Judge复现仓库扩展为Agent Safety Lab，新增离线工作台与G-Safeguard个人复现适配器。整理、编码和验证使用AI辅助，原论文方法不归本仓库作者所有。

| 材料 | 来源与范围 |
| --- | --- |
| R-Judge论文 | [Findings of EMNLP 2024正式版](https://aclanthology.org/2024.findings-emnlp.79/) |
| R-Judge原数据/评测 | 仍通过原有fetch脚本从上游获取；不因本仓库MIT许可而重新授权上游材料 |
| G-Safeguard官方实现 | [wslong20/G-safeguard](https://github.com/wslong20/G-safeguard)，独立获取固定提交，不在此重新分发MyGAT等官方源码 |
| MiniLM模型 | [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)，模型与依赖遵循各自许可，不随仓库上传权重 |
| 发布结果 | 由历史实验派生的ID、标注、预测与选项；不含原始长消息、问题正文、用户凭据或模型权重 |

根目录LICENSE沿用原有个人复现代码的MIT许可范围。上游代码、数据、论文与依赖不因目录归并而改变许可。界面图只用于复盘通信结构和答案，不宣称恢复了攻击因果链。
