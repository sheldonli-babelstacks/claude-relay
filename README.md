# claude-relay

把 GitHub／Jira 等外部 webhook 事件，实时转成对指定 Claude Code 云端会话的唤醒消息（`claude -p "<msg>" --cloud <session_id>`），不用轮询。支持同机多账号隔离。

**状态**：设计评审中，代码未开始。设计文档正在整理，评审通过后会放进这个仓（`docs/design.md`），并实现最小可用版本：

- 入口：GitHub webhook、Jira webhook、通用 `/send` HTTP API。
- 出口：对已有云端会话的 `--cloud` 唤醒（路由表里写死目标 session_id；本项目不依赖动态建会话，因为 `claude --cloud "<新会话描述>"` 需要交互式终端，脚本环境里用不了）。
- 多账号：每个账号一个独立 `CLAUDE_CONFIG_DIR`，互不污染；密钥/令牌只放运行环境变量，不进仓。
- 验签、去重、限流、JSON 日志。

## 许可证

MIT，见 [LICENSE](LICENSE)。

## 安全

本项目把外部 webhook 消息体当作不可信输入处理：转发给 Claude 会话的消息只拼接白名单字段，并标注消息来源，避免被注入指令。密钥/令牌只能通过部署环境的环境变量或 secrets 提供，任何情况下都不应提交进本仓或写进 issue。
