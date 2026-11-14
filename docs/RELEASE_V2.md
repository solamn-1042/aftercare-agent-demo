# v2 发布验收

代码提交：`95120f3d8c8cc56013a1d45c84a80833a9479549`。

[GitHub Actions 验收](https://github.com/solamn-1042/aftercare-agent-demo/actions/runs/43599516097) 的三个任务均已成功：

- case-lab：新增26项回归、离线检索评测、轻量容器构建、容器内HTTP创建及重复确认。
- regression：完整57项回归。
- container：原BGE/Chroma容器构建、真实Embedding与HTTP检查。

本机另外完成57项回归、真实HTTP smoke与Edge交互截图。模型评测为两轮各24次真实调用，记录分别位于 evals/baseline/lab_agent.json 和 evals/lab_agent.json。第二轮业务状态检查24/24，不能解释为完整语义准确率。

248条合成政策、252道自建问题；90道有答案留出题Hit@3为30%→90%，仍有9道失败。证据位于evals/lab_report.json。

发布后新增本文件仅记录上述已通过代码提交的验收，不改变运行代码或指标。
