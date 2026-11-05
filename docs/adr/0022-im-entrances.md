# ADR-0022：飞书、企业微信与 Telegram 入口

状态：采纳（NERE-116，M4-S2）；查阅日期：**2026-11-05**。
实施任务：S11（NERE-125）、S14（NERE-128）、S15（NERE-129）、S16（NERE-130）、S17（NERE-131）。
本 ADR 只确定入口方案，不表示真实应用已接通；本地和 CI 均不使用真实员工数据或平台凭据。

## 决定及依据

采用单实例 `im` Compose 服务，复用后端镜像，运行 `python -m app.im`。
飞书用官方 Python SDK 的长连接；企业微信用智能机器人 API 模式长连接；Telegram 用 HTTPS 长轮询。
三个入口的收文本、按钮回传、主动私聊推送和回复卡片均可在**不开放公网入站端口**的条件下完成。
主动推送须满足平台的可达条件；不能把凭据测试成功当成全部权限和消息收发均成功。

| 平台 | 收私聊文本 | 收按钮动作 | 主动推给单个用户 | 回复卡片 |
| --- | --- | --- | --- | --- |
| 飞书企业自建应用 | WSS `im.message.receive_v1` | 同一 SDK 的 `card.action.trigger` 长连接回调 | HTTPS `im/v1/messages`，用户须在应用可用范围内 | `msg_type=interactive` |
| 企业微信智能机器人 | WSS `aibot_msg_callback` | WSS `aibot_event_callback` 的 `template_card_event` | WSS `aibot_send_msg`；用户须先给机器人发过消息 | `aibot_respond_msg` 的 `template_card` |
| Telegram 普通机器人 | `getUpdates` 的 `message` | 同一轮询的 `callback_query` | `sendMessage`；用户须先建立与机器人的会话，且未屏蔽它 | 文本加 `InlineKeyboardMarkup`，不是飞书式卡片 |

官方依据（查阅 2026-11-05）：[飞书长连接事件][F1]、[飞书回调概述][F2]、[飞书发送消息][F3]、[企微长连接][W1]、[Telegram Bot API][T1]、[Telegram 机器人介绍][T2]。
下文编号 1–8 分别回答子 issue 的八类问题；“未验证”指文档未能完整读取或必须有真实应用才能确认，不把推测当作平台保证。

## 飞书（S14）

### 1. 无公网回调的能力

仅选**企业自建应用**，不选群自定义 Webhook 机器人或商店应用。
事件配置与回调配置都选长连接；只开事件订阅不会自动收到卡片按钮回调。
官方说明长连接可接事件和卡片回传交互；发送文本、卡片走 HTTPS API，接收通道不限制主动发送。
事件快速接收并交给处理循环，3 秒内完成接收响应；卡片回调立即反馈，不能等待 Pi 完成。
事件超时会补推，卡片同步回调没有补推保证，二者不能共用“等模型完成再确认”的处理方式。
依据（2026-11-05）：[长连接事件及限制][F1]、[回调订阅及同步响应][F2]、[发送消息][F3]。

### 2. 私聊与群聊

读取消息事件的 `event.message.chat_type`，只对 `p2p` 进入绑定和对话；`group` 只回复「请私聊我」。
按钮还要核对已登记的原消息、会话类型和操作者，不因为持有 token 就接受从群聊或转发卡片来的动作。
缺失或未知类型不处理画像。依据（2026-11-05）：[接收消息事件][F4]、[卡片回调结构][F5]。

### 3. 身份与企业邮箱

以事件发送者的 `open_id` 作为应用内外部用户标识，结合当前配置的应用和企业使用。
`open_id` 是应用维度的标识，换应用不能沿用绑定；`user_id` 是租户维度、`union_id` 是开发者维度，不混用，也不使用可修改的显示名。
企业邮箱不是消息事件自带字段。用应用令牌调用 `GET /open-apis/contact/v3/users/{open_id}?user_id_type=open_id`。
按权限页申请用户基本信息 `contact:user.base:readonly`；读取 `email` 要 `contact:user.email:readonly`，读取 `enterprise_email` 要 `contact:user.employee:readonly`，并配置通讯录可见范围。
这些是按字段授权，不要求通讯录写入或全员消息读取权限。
**未验证：**本次官方用户详情页正文未完整返回，权限名、字段返回和实际企业邮箱配置须由 S14 在测试应用核对；字段缺失、权限不足都回退绑定码，不宣称自动绑定必然可用。
只信平台返回的企业工作邮箱，不采信聊天里自报的邮箱，也不把个人邮箱静默当成企业邮箱。
仅在本企业 `work_email` 不区分大小写唯一匹配、员工有有效账号且自动绑定开关开启时绑定；停用、离职和重新建号均重新检查身份。
依据（2026-11-05）：[用户详情与字段权限][F6]、[用户 ID 说明][F7]。

### 4. 给管理员的配置步骤

1. 用有应用管理权限的账号进入[飞书开发者后台][F8]，创建「企业自建应用」，添加「机器人」能力，名称可设为「员工画像助手」。
2. 在「凭证与基础信息」复制 App ID、App Secret，在本平台「管理 → IM 入口 → 飞书」填写 `app_id`、`app_secret`。不用填写公网 URL、Verification Token 或 Encrypt Key。
3. 在飞书「权限管理」开通「读取用户发给机器人的单聊消息」`im:message.p2p_msg:readonly` 和「以应用的身份发消息」`im:message:send_as_bot`。如需在群里回复私聊引导，再开「接收群聊中 @ 机器人消息事件」`im:message.group_at_msg:readonly`，不申请全部群消息权限。
4. 要按邮箱自动绑定时，再开第 3 节的通讯录和字段权限，选择本企业测试员工的可见范围；不授权也能用一次性绑定码。
5. 在本平台勾选数据外发提示并开启，服务建立连接后，在飞书「事件与回调」的「事件配置」选择「使用长连接接收事件」，添加 `im.message.receive_v1`；在「回调配置」也选长连接，添加「卡片回传交互」`card.action.trigger`。后台若提示尚未建立连接，先检查本平台的连接状态。
6. 在「版本管理与发布」创建版本、提交管理员审核并发布，确认员工在应用可用范围内。员工搜索机器人并发起私聊，发送网页生成的绑定码。
7. 回到本平台点「连接测试」。S14 调用 `POST /open-apis/auth/v3/tenant_access_token/internal`，只显示固定中文结果；成功只证明应用凭据有效，仍需私聊收发和点击按钮验证订阅、权限及发布。

依据（2026-11-05）：[机器人能力与配置][F9]、[接收消息权限][F4]、[长连接配置][F1]、[回调配置][F2]、[获取应用令牌][F10]。

### 5. 卡片、长度与限频

使用交互卡片：预览提供「确认提交／继续修改」，状态卡提供「撤回」，问题卡提供「回答」。支持输入框，但默认统一用“点回答后在同一私聊发文本”，不使业务依赖平台表单。
**未验证：**新版卡片的按钮数量硬上限、`value` 单字段字节上限，以及发送消息当前大小和频控上限；官方相关页面本次只返回标题。
不沿用旧卡片文档或第三方转述的数字作为合同。S14 须在官方 API 调试台核对后，把边界写进适配器测试。
项目默认每卡最多 3 个动作按钮，`value` 仅包含短 token；每段文本最多 3000 字符、卡片序列化后最多 10 KiB，这是项目保守预算，**不是平台上限**。
大于预算分页或引导网页查看完整预览，不静默截断后允许确认；收到频控错误进入有上限的退避，不重试已成功的业务动作。
依据（2026-11-05）：[新版按钮][F11]、[输入框][F12]、[卡片结构][F13]、[发送消息及频控][F3]。

### 6. 依赖与维护

选官方 `lark-oapi==1.7.5`（查阅时最新版本，2026-10-15 发布），由 S14 添加并锁定，不手写长连接分包、鉴权和心跳协议。
核对 PyPI 的 1.7.5 wheel：内附 LICENSE 和元数据均为 **MIT**；官方仓库当前主分支 LICENSE 为 **Apache-2.0**，两者均兼容本项目，但实际安装物的许可证以锁定 wheel 为依据。
发布记录近期有更新；这不等于服务可用性保证。SDK 的同步请求、日志和连接关闭须在 S14 检查，禁止打印消息正文及凭据。
默认安装会带 `requests`、`requests-toolbelt`（Apache-2.0）、`pycryptodome`（BSD／公有领域）和 `websockets`（BSD），已有 `httpx` 为 BSD；不安装 Flask/FastAPI 等额外可选组。
该版要求 `websockets>=11,<16`，当前仓库锁为 16.0；S14 必须让包管理器重新解析、核对实际依赖许可证并跑回归，不能忽略此约束或单独复制 SDK 源码。
依据（2026-11-05）：[官方 SDK][F14]、[1.7.5 发布物与依赖][F15]、[仓库主分支许可证][F16]。

### 7. 出网与消息存储

服务器须允许到 `open.feishu.cn:443` 的 HTTPS 和 SDK 发现的 WSS 主机的 443 端口，不需要公网入站。
WSS 地址由服务发现返回，不把某一个 `msg-frontier` 主机名写成永久名单；严格出口网络由部署者核准飞书返回的主机名和证书后放行。
**未验证：**实际租户返回的完整 WSS 主机清单，真实测试时记录域名而非含凭据的完整连接 URL。
消息存在飞书平台和用户终端上，不因本平台清理聊天而消失；中国境内运营所收集的个人信息按飞书政策存于境内，不把此结论扩展到 Lark 国际版或其他数据驻留配置。
依据（2026-11-05）：[SDK 长连接与域名配置][F1]、[飞书隐私政策 §4.1][F17]。

### 8. 多连接行为

官方允许每个应用最多 50 条连接，但按集群随机分发，每个事件只给一个客户端，不是广播。
本项目仍只开一条；不能用“再起一个副本”实现可靠备份，否则事件和会话可能分散到不同进程。
同一应用凭据不跨本平台企业重复配置；换应用后旧绑定和按钮失效。
依据（2026-11-05）：[长连接注意事项][F1]。

## 企业微信（S15）

### 1. 无公网回调的能力

选择智能机器人 **API 模式 → 长连接**，地址 `wss://openws.work.weixin.qq.com`。
连接后用 `aibot_subscribe` 携带 `bot_id/secret` 订阅；消息、卡片按钮事件、卡片回复和主动推送均在该连接传送。
`aibot_send_msg` 支持 `markdown/template_card`，无需借用短期回调凭证；但**用户必须先在会话发过消息**，首次绑定消息可满足此条件。
不要把 URL 回调模式的 `response_url` 有效期或次数套到长连接；屏蔽、权限变化等发送失败仍由通知队列记固定错误码。
每 30 秒 `ping`；卡片点击更新 `aibot_respond_update_msg` 须在 5 秒内回复，不等待模型。纯文本按最终 Markdown／流式文本封装发送，平台传输形式不改变核心非流式协议。
依据（2026-11-05）：[长连接：认证、消息、卡片、主动推送][W1]。

### 2. 私聊与群聊

收消息看 `body.chattype`：`single` 才进入绑定和对话，`group` 只回「请私聊我」；群 `chatid` 不可当成员 ID。
发私聊指定 `chat_type=1`、`chatid=接收到的用户标识`，不省略类型使平台优先按群解析。
事件同样验证原会话，未知类型或非普通用户来源（`compat_info`）不进入画像流程。
依据（2026-11-05）：[长连接消息、事件与发送字段][W1]。

### 3. 身份与企业邮箱

用 `body.from.userid` 建绑定，结合企业和机器人配置。官方注明：机器人创建者为超级管理员时返回明文 userid，否则返回**企业主体下的加密 userid**，不能直接拿它查询通讯录。
需要时可用自建应用令牌调用 `POST /cgi-bin/batch/openuserid_to_userid` 转为明文，员工必须在该自建应用可见范围内。
读邮箱另需 `corp_id/contact_secret` 所代表的合法应用令牌与通讯录授权；机器人 Secret 本身没有这个权限。
更关键的是，新建自建应用的 `GET /cgi-bin/user/get` **不返回邮箱、企业邮箱**；官方要求管理员及员工本人 OAuth2 手工授权才能获取敏感字段，通讯录同步凭据还受调用 IP 历史限制。
因此 S5 的可空 `corp_id/contact_secret` 保留，但**不承诺填了就能自动绑定**；只在确实读到受授权的企业邮箱时沿用飞书的本企业匹配规则，否则绑定码。
M4 不新增 SSO/OAuth 登录流程来绕过限制。更换机器人创建者、应用或账号重建的标识连续性**未验证**，须重新验证或解绑重绑，不按姓名迁移绑定。
依据（2026-11-05）：[长连接发送者标识][W1]、[机器人 ID 转换与权限][W2]、[读取成员与敏感字段限制][W3]。

### 4. 给管理员的配置步骤

1. 由企业管理员进入企业微信管理后台的「智能机器人」管理，创建机器人；若从客户端操作，可从工作台的智能机器人管理进入编辑页。选择「API 模式」，连接方式选「长连接」，设置名称和可使用的员工范围。菜单位置随客户端版本变化，以 API 模式配置页为准。
2. 复制 BotID、长连接专用 Secret，在本平台「管理 → IM 入口 → 企业微信」填写 `bot_id`、`secret`。不用填写 Token、EncodingAESKey 或公网回调 URL；不要创建只有群 Webhook 地址的群机器人。
3. 默认使用网页一次性绑定码，不必申请通讯录权限。希望尝试企业邮箱自动绑定时，先核对第 3 节条件，再填可空 `corp_id/contact_secret` 并授权测试员工可见范围。新应用通常仍取不到邮箱，此时设置页应说明「无法读取企业邮箱，请使用绑定码」。
4. 勾选数据外发提示，保存并开启入口。不要同时把这组机器人凭据填入另一套系统；切换到 URL 回调模式会使长连接失效。
5. 点「连接测试」。S15 用实际运行连接的 `aibot_subscribe` 成功回执及心跳判断状态。尚未启用时可临时连接、认证后关闭；已连接时必须复用状态或由 `im` 执行测试，**不能另建测试连接把正式连接踢掉**。
6. 测试员工找到机器人私聊，发「绑定」加网页生成的六位码，再发一段合成经历，收到预览后点「确认提交」。另测群里只收到私聊引导。

依据（2026-11-05）：[长连接配置、凭据与模式互斥][W1]、[官方 SDK 的认证事件][W5]、[通讯录授权条件][W3]。

### 5. 卡片、长度与限频

用 `button_interaction` 模板卡片，`button_list` 最多 6 个；按钮 `key` 最长 1024 UTF-8 字节，`task_id` 最长 128 字节且同一机器人不能重复。
支持选择器、投票和多项选择，文档列出的模板没有自由文本输入框；回答、退回或拒绝原因在私聊输入，再展示确认卡。
Markdown 最长 20480 UTF-8 字节；同一会话的回复和主动推送合计最多 30 条/分钟、1000 条/小时。
按上述项目预算拆分，当前每卡最多 3 个动作，按钮仅放 token；模板卡片总大小硬上限**未验证**，S15 用真实应用测试边界，并保持超限时回到网页完整预览的降级。
依据（2026-11-05）：[模板卡片类型、Button、task_id][W4]、[长连接 Markdown 与频率限制][W1]。

### 6. 依赖与维护

HTTPS 通讯录查询沿用 `httpx`；它不提供 WebSocket 客户端，WSS 用 `websockets`，不为了消息封装增加第三方企微 SDK或 Node 服务。
`websockets` 已在仓库锁文件中，实施时须声明为直接依赖，版本与飞书 SDK 的 `<16` 约束一并解析；BSD 许可证兼容 Apache-2.0。
以企微官方 Node SDK 作为协议参照，**不安装它**；后续 S15 只实现订阅、心跳、消息／事件、回复、推送，不做媒体上传解密。
依据（2026-11-05）：[官方 Node SDK][W5]、[WebSocket 库与许可证][W6]、[长连接协议][W1]。

### 7. 出网与消息存储

机器人只需 `openws.work.weixin.qq.com:443` 的 WSS；可选读通讯录和 ID 转换再允许 `qyapi.weixin.qq.com:443` 的 HTTPS。
未实现的媒体下载不需要放行素材域名；不抓评论、文件或附件。
消息经企业微信平台及用户终端保存，不受本平台删除历史的控制。具体企业版本的数据驻留及保留期限**未验证**，管理员须核对自己的平台协议，不能把“内网部署”解释成消息只留在内网。
依据（2026-11-05）：[企微长连接域名及消息通道][W1]、[ID 转换 API 域名][W2]；存储核对入口：[企业微信官网][W7]的隐私与服务协议。

### 8. 多连接行为

每个机器人只能有一条有效连接；新连接订阅成功会踢掉旧连接，官方建议主备而非并连。
收到被接管的断开事件时停止盲目重连并显示固定状态，避免两个部署互踢；管理员必须先关闭旧部署再切换。
依据（2026-11-05）：[长连接数量限制][W1]。

### S15 实施记录（2026-11-05）

企业微信适配器复用共用对话、四类消息渲染和按钮校验，按钮 `task_id` 对应保存的消息标识。
启用时，设置页只读取 `im_setting.connection_checked_at`：认证成功和 30 秒心跳回执维护状态，超过 60 秒视为连接未就绪；不会为测试另建连接。
未启用时临时认证后立即关闭。该字段仅作运行状态，不保存凭据、消息或员工信息；迁移可往返。
通讯录只接受授权返回的 `biz_mail`，明文 ID 查询失败后尝试官方 ID 转换；权限不足、字段缺失、非唯一邮箱、无有效账号均回退绑定码，不采信个人邮箱或聊天自报邮箱。
当前卡片保守预算为 3000 字符且正文不超过 8000 UTF-8 字节；超限引导网页，卡片不提供确认按钮。
长连接及卡片使用本地假服务验证；真实应用的可见范围、通讯录授权及卡片显示仍需部署者验证。
保持 `im` 单实例，公网 URL 回调、媒体和多实例均不在 S15 范围；主动推送和审批复用其他子任务的服务。

## Telegram（S11）

### 1. 无公网回调的能力

用 `getUpdates` 长轮询收 `message` 和 `callback_query`；HTTPS `sendMessage/editMessageText/editMessageReplyMarkup` 发或更新卡片式消息，并用 `answerCallbackQuery` 及时消除按钮等待状态。
不依赖 Web App、内联模式或公网 Webhook。机器人不能任意开启与用户的私聊，员工先点击 Start 并发绑定码；被屏蔽后通知发送可能失败。
依据（2026-11-05）：[Bot API 获取更新与发送方法][T1]、[机器人使用条件][T2]。

### 2. 私聊与群聊

仅 `message.chat.type=private` 进入绑定和对话；`group/supergroup/channel` 不处理画像，群消息只引导私聊。
按钮核对 `callback_query.from.id`、其 `message.chat.type` 和原消息映射；没有原消息的 inline 回调拒绝，不能信任 `callback_data` 指定的身份。
依据（2026-11-05）：[Chat、CallbackQuery 结构][T1]。

### 3. 身份与邮箱

用数值 `User.id`，在数据库以字符串保存；`username` 可修改且可能不存在，不能用于绑定或授权。
私聊发送目标取已验证的 `chat.id` 并记录映射，不从员工填写的用户名猜测。
Bot API 的 User 不提供企业邮箱，不申请手机号或让员工发邮箱来模拟自动绑定，始终用一次性绑定码。
换 bot token 后先用 `getMe` 核对是否仍为同一个机器人；换成另一机器人则旧会话、动作和推送目标不自动迁移。
依据（2026-11-05）：[User、Chat、getMe][T1]。

### 4. 给管理员的配置步骤

1. 在 Telegram 找到官方认证的 [@BotFather][T4]，发送 `/newbot`，按提示填写名称及以 `bot` 结尾的唯一用户名，复制生成的 token。
2. 在本平台「管理 → IM 入口 → Telegram」填写 `bot_token`；无需 App ID、手机号、API ID 或 API Hash，也不用把机器人设为群管理员。
3. 保持 BotFather 的群隐私模式开启；它不替代本平台的私聊检查。不要把同一 token 交给另一套机器人程序。
4. 阅读并勾选「消息会存在 Telegram 服务器上；服务器在境外，中国大陆通常连不上」，确认部署服务器具备可达出口后开启。M4 不提供代理服务器配置。
5. 点「连接测试」，调用 `getMe` 验证 token；开启收消息前读取 `getWebhookInfo`，若已有 Webhook 则提示先由管理员停用原集成，不擅自删除别人的配置。
6. 员工打开机器人私聊，点 Start，在网页生成绑定码后发送「绑定 123456」。随后用合成内容测试预览和确认，再验证提醒推送。

依据（2026-11-05）：[BotFather 与隐私模式][T3]、[getMe/getWebhookInfo][T1]。

### 5. 按钮、长度与限频

`callback_data` 为 **1–64 字节**，不是字符；`sendMessage` 文本为 1–4096 字符（解析实体后）。
Inline keyboard 是按钮行数组，没有自由文本输入框；回答按钮之后在同一私聊输入文本。当前官方 API 没给通用按钮总数硬上限，**未验证**，项目每卡最多 3 个动作，真实应用测布局即可。
按官方建议，同一聊天避免超过 1 条/秒，群聊不超过 20 条/分钟，免费广播约 30 条/秒；本项目不启用付费广播，429 按 `retry_after` 延迟。
文本默认 3000 字符一段，正确转义格式；过长预览分页或回网页，不以截断的内容作为完整确认依据。
依据（2026-11-05）：[InlineKeyboardButton、sendMessage、ResponseParameters][T1]、[键盘交互][T3]、[官方频控建议][T5]。

### 6. 依赖与维护

复用已有 `httpx` 调 Bot API，不加 `python-telegram-bot` 或 `aiogram`。
设置 HTTP 超时大于长轮询 timeout，处理 API 的 `ok=false`，只记录固定分类；token 在 URL 路径里，尤其要屏蔽 HTTP 客户端、代理及异常的完整 URL 日志。
依据（2026-11-05）：[官方 HTTP Bot API 协议][T1]；[HTTPX 官方说明与 BSD 许可证][T6]。

### 7. 出网与消息存储

服务器放行 `api.telegram.org:443` 的 HTTPS；`t.me/telegram.org` 供管理员和员工打开说明及客户端，不是 Bot API 的附加服务依赖。
聊天属于 Telegram 云消息，存在其平台服务器和终端；不是端到端加密的 Secret Chat，部署本地 Bot API 也不能把 Telegram 聊天变成本地存储。
数据在境外、中国大陆通常连不上是本项目必须展示的部署提示；具体机房不能由项目指定，实际网络可达性**未验证**，须从部署服务器测试 `getMe` 和长轮询，而非只看管理员电脑能否打开客户端。
依据（2026-11-05）：[Bot API 域名][T1]、[Telegram 隐私政策 §3.3、§5][T7]、[云消息说明][T8]。

### 8. 多连接行为

Webhook 与 `getUpdates` 互斥；一个 bot 只运行一个轮询消费者，多个进程会竞争同一更新队列，不能靠不同 offset 实现广播。
`offset` 在接收记录持久化成功后推进为 `update_id+1`；先落接收记录再交核心，重复 update 不重复执行动作。Telegram 只保留未取更新最多 24 小时，长时间停机不能保证补齐。
并行轮询是否必然返回 409 **未验证**；实现不依赖某个错误码来保证单实例，真实测试另测竞争结果。
依据（2026-11-05）：[Getting updates、getUpdates 的确认与保留规则][T1]。

## 共用进程、接口与动作

采用 issue 推荐的单进程方案；HTTP 后端、队列 worker 不启动 IM 接收循环，不引入 Redis。
每个开启的 `(org_id, platform)` 一条连接／轮询任务；每 30 秒读一次设置版本，只有启停、凭据或相关设置变化才重连，不每 30 秒无条件断连。
禁用、解绑和账号停用在分发／发通知前再次检查；关闭连接、停止自动重连并作废未发送通知，不能在设置轮询窗口内继续处理旧权限。
同一外部机器人只配置给一个企业；启动时检测同平台应用／机器人身份重复并拒绝第二条，防止消息归属歧义。

绑定码失败限频同时按 IM 账号和企业加平台累计（默认分别为 5 次、300 次，窗口为 `LOGIN_LOCK_MINUTES`），防止换账号绕过；代价是攻击者可能让该企业的该平台在一个锁定期内暂时无法绑定。

S11 的绑定保存已验证的 `external_chat_id` 和 `getMe` 返回的 `bot_identity`；
`im_setting.bot_identity` 仅缓存当前连接已核实的机器人身份，供网页显示绑定是否失效，
凭据变化时清空，不能代替分发时重新计算 Principal。
`im_event.event_id` 以机器人身份限定平台事件 ID，避免换机器人后事件编号重叠；
收到事件先持久化去重，再处理文本，保留 7 天。此入口按至多一次接收处理，
进程在记录事件后崩溃可能丢失该条回复，用户可重发；这不作为 S11b 业务动作事务的实现。
S11 只提供绑定和固定文本说明；对话、按钮及下表中的卡片渲染由 S11b（NERE-141）交付。

入口接收后先验证平台配置、会话类型和外部发送者，再由绑定关系取得员工、成员和用户，每轮按 `credential_principal` 规则重算 Principal。
非成员、停用、须改密码、未绑定均不进入核心；无绑定只给说明。群聊只说「请私聊我」，不能完成绑定或显示卡片。
调用 `backend/app/domains/bot/core.py` 的 `turn/act`，不模拟网页登录、不另写提交或审批事务。
沿用 [ADR-0018](0018-bot-conversation-contract.md) 的四种核心消息；经理审批的 `review_card/confirm_review` 由 S17 增补 ADR-0018。

| 适配器职责 | 统一约定 |
| --- | --- |
| 接收 | 将文本／按钮转为企业、平台、外部操作者、私聊会话、平台事件 ID 和内容；事件 ID 用于去重，不承担授权 |
| `send_text(external_chat_id, text)` | 使用已验证绑定／私聊映射中的聊天标识发文本；S16 仅发类型、简短说明及查看引导，不发证据正文或画像陈述 |
| 渲染四类消息 | `text`、`preview_card`、`status_card`、`question_card`；只渲染核心明确列出的动作，不把模型文字解释为命令 |
| 连接测试 | 飞书令牌、企微订阅及心跳、Telegram `getMe`；超时有界，结果用固定中文分类，不透传上游正文 |

按钮携带随机短 token：推荐 `token_urlsafe(16)` 的 22 个 ASCII 字符，各平台负载都能容纳；不是拼接请求 JSON、邮箱或请求 ID。
服务端映射到 `(org, platform, bot identity, employee, session, external chat, message, action, request revision, expires_at)`，期限不长于核心卡片历史保留期。
回调同时检查操作者、绑定、私聊、当前权限和 revision；核心仍验证真实展示过的卡片。无效、过期、转发给他人或跨企业 token 一律不执行。
业务成功后在同一持久化边界标记动作已消费，重复点击返回已处理状态，不能先消费再丢失动作；重启也不能重新执行已完成动作。
S11b 实施：`bot_session` 同时保存渠道、外部聊天和机器人身份；换机器人后不复用旧会话。核心 `act` 默认照旧提交事务，IM 入口选择由调用方把业务写入与 `im_action.consumed_at` 一起提交。动作过期时间以原卡片历史的创建时间和企业保留设置计算，并由原历史清理任务删除过期映射。
“回答”先建立本会话待答问题状态，再把用户文本交 `answer_question`；不把问题答案塞到按钮里。
员工提交沿用 `via=bot`、客户端「内置机器人（飞书／企业微信／Telegram）」；S17 审批才用 `via=im` 和平台名称，绝不替本人批准。

IM 适配器只做输出投影，不获取额外证据正文；S17 严格按其白名单外发，restricted 正文和员工依据说明转网页查看。
本平台历史沿用加密存储、闲置 2 小时删除及企业可选最长 30 天保留；平台侧消息不随之删除。
认证失败或被接管停连，普通网络断开用有上限的指数退避；按平台事件 ID 持久化去重，快速接收不等同于业务已完成。
本期不做多副本、高可用或新通用消息总线；后续扩容须另写 ADR，不能直接扩大 Compose 副本数。

## 公网回调替代方案

目前四项能力均不要求公网回调，M4 默认不实现此分支。遇到租户确实无法启用长连接时，先记录缺口，不自动改成 Webhook。
选择替代方案前需公开可访问的 HTTPS 域名、有效证书和反向代理，并由管理员配置独立的最小回调路由；只暴露接收入口，不暴露管理接口。
飞书须完成 URL challenge、配置 Verification Token／Encrypt Key、按官方方案验签和解密；企业微信 URL 模式另需 Token／EncodingAESKey、URL 校验和消息解密，不能复用长连接 Secret。
Telegram 改用 `setWebhook` 时校验 `X-Telegram-Bot-Api-Secret-Token`，同时停止轮询；官方支持 443、80、88、8443 等指定端口，项目选择 443。
三者都要限制请求体、及时响应、去重和复查绑定；具体路由、凭据扩展和端口部署另行评审，S5 本期不增加这些字段。
依据（2026-11-05）：[飞书回调方式][F2]、[企微长连接与 URL 模式区别][W1]、[企微回调加解密][W8]、[Telegram setWebhook][T1]、[Webhook 排障][T5]。

## 真实验证需要的应用及交付边界

大古需提供下列**专用测试资源**，密钥通过本平台设置页或本地测试环境注入，不放在 issue、PR、仓库、日志中：

| 平台 | 所需资源 | 实际验证内容 |
| --- | --- | --- |
| 飞书 | 一个企业自建应用及管理员；两个合成员工账号（其中一个可作经理）；可发布版本及批准上述权限；可选企业邮箱 | 单聊文本、按钮回调、主动提醒、群聊拒绝；企业邮箱字段权限；长连接域名；卡片大小、按钮和频控边界 |
| 企业微信 | 一个 API 长连接智能机器人、管理员及两个测试成员；可选受授权自建应用／通讯录凭据 | 订阅、消息、卡片、主动推送前置条件；连接测试不互踢；明文／加密 userid 转换；邮箱缺失回退；卡片边界 |
| Telegram | 一个 BotFather 测试机器人及两个测试账号；部署服务器可达 Bot API | Start 后收发、64 字节边界、按钮回调、屏蔽后发送失败、offset 重放、并行轮询竞争、断网恢复 |

本次没有这些测试应用，**未做真实平台调用**，只查公开官方文档、SDK 和发布物。
S11/S14/S15 本地与 CI 默认用 HTTP/WSS 假服务器（飞书可用 SDK 适配器替身），不得对外发起真实员工或模型请求。
至少覆盖：正确／错误凭据及脱敏、私聊／群聊、绑定码过期重用、未绑定／解绑／停用、邮箱缺失、跨企业／他人 token、旧 revision、重复事件和点击、频控退避、设置变化停连、四类渲染及通知边界。
S17 再覆盖三平台经理确认与禁止自批；假服务器通过不算真实平台权限、布局或网络验收。
未验证项在各实施 PR 写明；缺少邮箱不阻断绑定码入口，平台硬边界不得冒充已实测。

## 官方来源

以下均于 2026-11-05 查阅；企微正文由官方页面 HTML 读取，飞书部分动态页面只返回标题，相关未确认项已在上文标明。

[F1]: https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-
[F2]: https://open.feishu.cn/document/event-subscription-guide/callback-subscription/callback-overview
[F3]: https://open.feishu.cn/document/server-docs/im-v1/message/create
[F4]: https://open.feishu.cn/document/server-docs/im-v1/message/events/receive
[F5]: https://open.feishu.cn/document/feishu-cards/card-callback-communication
[F6]: https://open.feishu.cn/document/server-docs/contact-v3/user/get
[F7]: https://open.feishu.cn/document/home/user-identity-introduction/open-id
[F8]: https://open.feishu.cn/app
[F9]: https://open.feishu.cn/document/uAjLw4CM/ukzMukzMukzM/develop-robots/bot-overview
[F10]: https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal
[F11]: https://open.feishu.cn/document/feishu-cards/card-components/interactive-components/button
[F12]: https://open.feishu.cn/document/feishu-cards/card-components/interactive-components/input
[F13]: https://open.feishu.cn/document/feishu-cards/card-json-v2-structure
[F14]: https://github.com/larksuite/oapi-sdk-python
[F15]: https://pypi.org/project/lark-oapi/1.7.5/
[F16]: https://github.com/larksuite/oapi-sdk-python/blob/main/LICENSE
[F17]: https://www.feishu.cn/privacy?lang=zh
[W1]: https://developer.work.weixin.qq.com/document/path/101463
[W2]: https://developer.work.weixin.qq.com/document/path/101521
[W3]: https://developer.work.weixin.qq.com/document/path/90196
[W4]: https://developer.work.weixin.qq.com/document/path/101032
[W5]: https://github.com/WecomTeam/aibot-node-sdk
[W6]: https://github.com/python-websockets/websockets
[W7]: https://work.weixin.qq.com/
[W8]: https://developer.work.weixin.qq.com/document/path/101033
[T1]: https://core.telegram.org/bots/api
[T2]: https://core.telegram.org/bots
[T3]: https://core.telegram.org/bots/features
[T4]: https://t.me/BotFather
[T5]: https://core.telegram.org/bots/faq
[T6]: https://github.com/encode/httpx
[T7]: https://telegram.org/privacy
[T8]: https://telegram.org/faq
