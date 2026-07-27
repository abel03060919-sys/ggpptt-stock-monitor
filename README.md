# ggpptt.store 到货提醒

每 5 分钟检查商品 `GPT RT Plus 成品号（欧洲渠道）`（ID 68）。补货时使用 Gmail 给自己发送一次邮件；持续有货不会重复发送。

## 启用

1. 在 GitHub 新建一个 **Public** 仓库，并把本项目推送到默认分支。
2. 在 Google 账号中开启两步验证，然后按照 [Google 官方说明](https://support.google.com/mail/answer/185833?hl=zh-Hans) 创建“应用专用密码”。
3. 打开 GitHub 仓库的 `Settings → Secrets and variables → Actions`。
4. 新建两个 Repository secrets：
   - `GMAIL_ADDRESS`：你的完整 Gmail 地址。
   - `GMAIL_APP_PASSWORD`：Google 生成的应用专用密码，不是 Gmail 登录密码。
5. 打开仓库的 `Actions → Stock monitor → Run workflow`，勾选测试邮件并运行。
6. 收到“到货监控测试邮件”后配置完成，之后无需保持电脑开机。

## 提醒规则

- 库存从 0 变为大于 0：发送一次邮件。
- 库存持续大于 0：不重复发送。
- 库存再次变为 0：重置，等待下一次补货。
- 公开仓库每 30 天自动提交一次非敏感保活日期，避免 GitHub 因 60 天无活动停用定时任务。

## 安全

邮箱地址和应用专用密码只保存在 GitHub Secrets 中。代码、`state.json` 和运行日志不会输出这些值。

## 手动检查

在 Actions 页面运行 `Stock monitor`。不勾选测试邮件时，它只执行一次正常库存检查。
