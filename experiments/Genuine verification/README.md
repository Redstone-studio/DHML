# Mosslight 正版验证实验

一个**独立的小项目**，用来试"正版验证"到底该怎么做。界面是空壳，重点是验证那套东西。

> ⚠️ 这个目录**只从主项目 `D:\DHML-src` 复制**样式和头像模块，从不改动主项目。

## 怎么跑

```powershell
cd "D:\DHML-src\experiments\Genuine verification"
pip install -r requirements.txt
python main.py
```

自测（不弹窗口）：

```powershell
python -m core.verify.selftest       # SLP / Query 协议（离线假服务器）
python -m core.verify.selftest_ali   # authlib-injector 协议 + 外置登录验证器（离线）
python -m core.verify.selftest_ms    # 微软六步链路 + 账户存储（离线假服务器）
python -m core.verify.yggdrasil <站点> [角色名]   # 查真实验证站
python -m core.verify.mcping <服务器>             # 查真实服务器在线名单
python -m core.verify.srv <域名>                  # 查 SRV 记录
python tools\smoke_test.py           # 界面：窗口/样式/三套面板/账户页/Fusion
python tools\e2e_test.py             # 整条链路：假服务器 → 验证器 → 界面 → 头像
```

## 现在有三套验证方式

| | 微软登录 | 外置登录（authlib-injector） | 玩家列表（古法） |
|---|---|---|---|
| 用户要填 | 自己的 Azure 客户端 ID | 验证站地址 + 角色名（+ 可选密码） | 服务器地址 + 玩家名 |
| 谁在验证 | **Mojang/微软** | **第三方验证站** | **Mojang/微软**（服务端代劳） |
| 证明的是 | 这是**正版**账号，而且是你本人的 | 账号**在那台站上存在**，填密码才证明是你的 | 这是**正版**账号 |
| 是不是真·正版验证 | ✅ 会查 entitlements | ❌ 和 Mojang 无关 | ✅ 但只在正版服上有意义 |
| 需要密码 | 走微软官方页面，密码不经过我们 | 查名字不用；证明归属要 | 不要 |
| 交互方式 | **交互式**（设备码 + 轮询） | 一次查询 | 一次查询 |

⚠️ **三种的结论含金量完全不同。** 界面上每一处都写明了这次通过到底意味着什么。

---

## 一、微软登录（正版）

### ⚠️ 先说结论：老办法（复制链接）已经废了

老流程是让浏览器最后跳到
`https://login.live.com/oauth20_desktop.srf?code=...`，用户把整条 URL 复制回启动器。
**这条路现在走不通** —— 微软会把它重定向成 `oauth20_desktop.srf?removed=true`，
code 拿不到。

现在有两条正路（[Minecraft Wiki](https://minecraft.wiki/w/Microsoft_authentication)
列的就是这两条）：

| 走法 | 用户体验 | 说明 |
|---|---|---|
| **设备码**（本实验用的） | 显示一串码，用户去 `microsoft.com/link` 输入 | 不用本地端口、不用复制长 URL。**PCL CE 用的也是这个** |
| 授权码 + 回环 | 自动开浏览器，授权完自动跳回来 | 最顺，但要本地起临时 HTTP 服务器接回调 |

设备码其实**比"复制链接"更接近**用户原来的体验：一样是开浏览器登录，
只是把"复制一长串 URL"换成了"输入 6 个字符的码"，而且不用手抄 URL。

### 六步链路

```
1. OAuth2 拿微软 access_token      login.microsoftonline.com/consumers/oauth2/v2.0/devicecode → /token
2. Xbox Live 认证                 user.auth.xboxlive.com/user/authenticate
3. XSTS 授权                      xsts.auth.xboxlive.com/xsts/authorize
4. Minecraft 登录                 api.minecraftservices.com/authentication/login_with_xbox
5. 查是否拥有游戏                  api.minecraftservices.com/entitlements/mcstore   ← 只有这步算"正版"
6. 查档案（名字/UUID/皮肤）         api.minecraftservices.com/minecraft/profile
```

**第 5 步不能省。** 前面四步**任何普通微软账号都能走通** —— 没买游戏的账号
一样能拿到 MC access_token。所以"能登录"和"有正版"是两件事。

**每一步的失败原因完全不一样**，所以错误里带 `step`（卡在第几步）、`code`、
以及一句 `hint`。糊成"登录失败"等于没说。

### 微软登录怎么开通（这就是你要的那份步骤）

> 我按 [Minecraft Wiki](https://minecraft.wiki/w/Microsoft_authentication) 和
> [Microsoft Q&A](https://learn.microsoft.com/en-us/answers/questions/5768276/how-to-get-xboxlive-signin-permission-for-azure-ap)
> 整理的，**两个硬门槛都在第 4 步**。

**① 注册 Azure 应用**（不用客户端密码）

`portal.azure.com` → Microsoft Entra ID → 应用注册 → 新注册

- **支持的账户类型**：选「仅此组织目录中的账户」之外那个 ——
  要能登**个人微软账号**（代码里写死了 `consumers` 租户，见下）
- **不需要**创建客户端密码（公共客户端不用密钥）

**② 开「允许公共客户端流」**

应用 → 身份验证 → 最下面「允许公共客户端流」→ **是**
（设备码流程靠它；不开的话申请设备码直接报 `invalid_client`）

如果要走"授权码 + 回环"，再加平台「移动和桌面应用程序」，
勾上 `https://login.microsoftonline.com/common/oauth2/nativeclient`。

**③ 复制「应用程序(客户端) ID」** → 填进本程序「微软登录」页那个输入框

**④ ⚠️ 申请 Minecraft API 权限（最容易被忽略、也是最卡人的一步）**

去 <https://aka.ms/mce-reviewappid> **填表申请**。

**不申请的话第 4 步一定返回 403 `Invalid app registration`** ——
光在 Azure 门户里配权限是不够的，Mojang 另外加了一道人工审核。

#### 这一步到底是什么（**不是**付费，**不是**游戏开发）

它不是 Azure 的付费内容，也不是"做游戏才能用"。它是一张
**Mojang 维护的白名单**：允许调用 `api.minecraftservices.com` 的应用名单。

网上流传的说法（包括 Microsoft Q&A 上某个 moderator 的答复）是
"要加入 **Xbox Developer program / ID@Xbox**" —— **这个说法是错的**。
有开发者（Minelys Launcher）真的去问了 ID@Xbox，得到的原话是：

> Sorry, but this is not something the ID team can help with.
> **Minecraft is not managed by the ID team.**

#### 真正的路，而且**有人成功了**

另一位开发者（louka）在同一条帖子里给出了成功经验（2026-08-29）：

> The solution was to submit the application's Client ID for approval through
> Minecraft's official **AppID registration process**.
> After submitting the form, I received an email from **Mojang Enforcement**
> confirming that the application had met the required criteria and had been
> **approved for their allow list**.

流程：

```
1. 填表   https://aka.ms/mce-reviewappid
2. Mojang Enforcement 审核
3. 通过 → client_id 进白名单 → 第 4 步的 403 消失
```

**关键是"有人成功了"** —— 不是"理论上有这条路"。

#### 审核看的是「你的启动器」，不是「你做过什么游戏」

申请的**主体是一个应用（Client ID）**，不是一个人或一家公司。审核问的是
"这个应用是不是一个正经的 Minecraft 启动器"，**不是**"这个开发者做过什么级别的游戏"。

⚠️ 还有个常见误解：以为"做出一款大作 → 进 ID@Xbox → 就能拿到 Minecraft 权限"。
**这条链是断的** —— ID@Xbox 自己说了"Minecraft 不归 ID 团队管"。
这两件事完全独立，进了 ID@Xbox 也拿不到 MC 白名单。

（顺带：Playground Games / Turn 10 那些是**微软第一方工作室**，
走的是内部通道，压根不存在"申请白名单"这回事 —— 而且 Forza 跟 MC 账号认证
毫无关系，拿它类推没有意义。）

**反过来说这是好消息**：那个成功案例听起来就是个**独立开发者的小启动器**，
不是 AAA。所以门槛更可能是"是不是真的启动器 / 有没有守规矩"，
而不是"作品够不够牛"。

#### 还搞不清的一点

**审核标准没有公开。** 有独立开发者在问"没有 D-U-N-S（企业编码）
的个人开发者有没有人工核验通道"，**这个问题目前没人回答**。
所以：个人可能能过，也可能要企业身份 —— 但**填表不花钱、不绑卡，值得一试**。

所以现实可行的路有三条：
  · 填表申请（有成功案例）
  · 你自己已经有/能拿到一个**已获批**的 client_id
  · 或者用**外置登录**（authlib-injector）那条 —— 它不需要微软批任何东西

**⑤ 另外两条硬规矩**（代码里已经写死，但知道一下有好处）

- **必须用 `consumers` 租户**。带 AAD 租户 ID 或 `common` 会直接报错，
  而且只能登个人微软账号（企业账号进不来）
- **scope 必须带 `XboxLive.signin`**，否则第 2 步会以很难懂的方式报错

### ⚠️ 卡在门户登录：`AADSTS16000` / `ADIBizaUX`

如果你在 **Azure 门户里**就看到这么一段（**不是本程序弹的**）：

```
interaction_required: AADSTS16000: User account from identity provider 'live.com'
does not exist in tenant 'Microsoft Services' and cannot access the application
'74658136-14ec-4630-ad9b-26e160ff0fc6'(ADIBizaUX) in that tenant.
```

**这不是 bug，也不是我们造成的** —— `ADIBizaUX` 是**门户自己**的客户端 ID。
原因是：

- 用**个人微软账号**（Outlook / Hotmail / live.com）登门户时，
  会自动连到 **"Microsoft Services" 系统租户**（ID `f8cdef31-a31e-4b4a-93e4-5f571e91255a`）
- **这个租户没有目录**。所以在里面**什么管理操作都做不了**：
  建用户、建组、**注册应用**，全部不行
- 去 Entra ID → 概览能看到租户 ID 就是这个；设置里能看到没有关联目录

**解决办法：自己建一个租户**（免费，不需要买任何东西）

1. 开一个 **InPrivate / 无痕**窗口（避免 SSO 自动带登录态 —— 不清干净会一直复现）
2. 去 <https://azure.microsoft.com/free/> 点「免费开始使用 / Try Azure for free」
3. 注册流程里会让你**新建一个目录（租户）**，完成后你就是这个租户的**全局管理员**
4. 之后再去「应用注册」就能建应用了

（另一条路：<https://portal.azure.com/#create/Microsoft.AzureActiveDirectory>，
但它需要你已经在一个有目录的租户里。所以第一次还是得走上面那条。）

> 参考：[Can't use Azure Portal](https://learn.microsoft.com/en-au/answers/questions/5846780/cant-use-azure-portal)
> 和 [Unable to create OAuth app due to login errors](https://learn.microsoft.com/en-ca/answers/questions/5519679/unable-to-create-oauth-app-due-to-login-errors)
> —— 微软支持人员的答复，两处说法一致。

⚠️ **但先想清楚值不值得**：建好租户、建好应用之后，**还有第 ④ 步那道坎**
（Minecraft API 权限白名单）。不过那道坎**不是"要付费"也不是"要做游戏"**，
就是填个表等审核 —— 有成功案例（见上面 ④ 的说明）。
如果只是想有个能跑的正版验证，**外置登录那条已经完全可用**，不用等审核。

#### 那建租户要钱吗？

**注册应用本身不要钱**，但要先有个租户，而"建租户"这个入口有门槛：

| 环节 | 花钱吗 |
|---|---|
| Entra ID **Free 层** | 不要钱（P2 的要求 2021 年就取消了） |
| **应用注册** | 不要钱 |
| **建租户** ← 卡在这儿 | 见下 |

官方给的[两条路](https://learn.microsoft.com/en-us/entra/verified-id/how-to-create-a-free-developer-account)都有门槛：

1. **Azure 免费账户** → 建租户，拿到 Entra ID Free 层。
   **要信用卡**（只做身份验证，免费额度内不扣钱，但得有张国际卡）
2. **Microsoft 365 开发者计划** → 免费 E5 沙箱租户。
   **现在要资格了**：官方原文是
   > Eligible members include Visual Studio Enterprise or Professional subscribers,
   > ISV Success Program or Microsoft AI Cloud Partner Program participants,
   > and Premier or Unified Support customers.

   也就是说**以前随便谁都能领的免费租户，现在个人爱好者不符合条件了**。

**唯一不要卡的路**：如果你是学生，用**学校邮箱**开
[Azure for Students](https://azure.microsoft.com/free/students/) —— 它明确不要信用卡。

#### ⚠️ 虚拟卡 / 预付卡这条路是死的

想用第三方平台的"虚拟信用卡"绕过的话，**别花这个钱**。微软官方文档写得很直白：

> **You're using a virtual or prepaid card**
> Prepaid and virtual cards are **not accepted** as payment for Azure subscriptions.
>
> —— [Troubleshoot a declined card](https://learn.microsoft.com/en-us/azure/cost-management-billing/troubleshoot-billing/troubleshoot-declined-card)

虚拟卡平台收你几十到几百块，Azure 在验证那一步直接拒，而且用虚拟卡注册还违反条款、
建成的租户也可能被封。

**但不需要信用卡** —— 同一份文档里有这句：

> Credit cards are accepted and **debit cards are accepted by most countries or regions**.

所以**支持境外支付的 Visa/Mastercard 借记卡**也可以用（成本为零，值得一试）。
注意文档特别强调两点：卡要**开通了境外交易**，而且**姓名/地址/CVV 必须和卡上完全一致**。
（银行的"电子卡 / 数字借记卡"也算借记卡，只要能跨境授权就行。）

#### 那到底会不会被扣钱？

**不会 —— 只要你不点「升级」。** 微软官方文档
[Avoid charges with your Azure free account](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/avoid-charges-free-account)
写得很清楚：

> As long as you have unexpired credit or you use only free services within the
> limits, **you're not charged**.
>
> Your subscription and services are **disabled** when your credit runs out or
> expires at the end of 30 days. **To continue using Azure services, you must
> upgrade your account.**

关键是**"停用"而不是"扣费"**：额度用光或 30 天到期，服务被停掉，
要开始付费得你自己主动 upgrade。

而且**这个项目根本不建任何计费资源**：

| 我们需要的 | 费用 |
|---|---|
| 一个租户（Entra ID 目录） | **免费** |
| 一个应用注册 | **免费**（Entra ID Free 层自带） |

不建虚拟机、不建存储、不建任何 Azure 资源。应用注册只是一条身份记录，
不消耗任何东西 —— 账单会一直是 $0.00。

**填卡时那笔小额预授权不是扣费**：它是验证卡有效、能跨境交易的"冻结"，
常见 1 美元上下，一般几天内自动撤销。看到短信别慌。
（做不了预授权的借记卡会在这一步失败 —— 那是卡不支持，不是被扣了钱。）

**注册页上那句官方承诺**（原文，就写在你眼前）：

> **支出保护 — 在你转到即用即付定价之前，我们不会向你的信用卡自动收费**
> Spending protection — we won't charge your credit card automatically
> until you move to pay-as-you-go pricing

**"自动"是关键词。** 它和上面那份文档是同一套机制：额度用完/到期 → **停用服务**，
而不是扣钱。要开始付费得你自己主动升级。

⚠️ 但**这个保护有三个不管的地方**，别当成万能：

| 情况 | 保护还在吗 |
|---|---|
| 免费账户用超了 | ✅ 在 —— 服务被停，不扣钱 |
| 30 天到期 / $200 用完 | ✅ 在 —— 服务被停，不扣钱 |
| **你点了「升级到即用即付」** | ❌ **没了** |
| **你买了 Marketplace 里的第三方商品** | ❌ **没了**（不算在免费额度和保护里） |

所以规矩只有一条：**别点升级**。

**"会不会哪天变卦"**：条款确实可以改，但这条承诺存在很多年了，
我引的文档是 2026-03 更新的、说法一致；而且**你随时能取消订阅**。
另外你用的是**借记卡** —— 账户里有多少就只能扣多少，没有信用额度可透支，
本身就多一层物理上限。

**三重保险**：

1. **绝不点「Upgrade / 升级」** —— 那是唯一的付费开关
2. **设预算警报**：Cost Management → Budgets，额度填 `$0.01` + 绑邮箱，
   有任何一分钱消费立刻通知
3. **用完取消订阅**：Subscriptions → Cancel subscription，卡就彻底解绑了

#### 那"免费"为啥还要绑卡？

因为**卡的作用是身份验证，不是收款**。最直接的证据是对比：

| | 要不要卡 | 要什么 |
|---|---|---|
| Azure 免费账户 | **要** | 信用卡 / 借记卡 |
| Azure for Students | **不要** | **学校邮箱** |

学生不要卡但要邮箱 —— 说明微软要的是"**一个能证明你是谁的凭证**"，
学生有学校邮箱，其他人就只能拿卡来证明。

具体三个原因：

1. **防刷免费额度**（最主要）：$200 是真金白银，不绑卡的话可以无限注册小号刷额度
   （挖矿、发垃圾邮件、跑代理）。而且卡的**姓名/地址必须和你填的完全一致** ——
   这是身份核验的特征，不是收款的特征。
2. **预置一个支付方式**：额度用完后你还想继续用，绑卡省得重走流程。
   所以它是"预存的支付方式"，不是"立即的收款"。
3. **行业惯例**：AWS Free Tier、Google Cloud、阿里云国际站**全都要求绑卡**。

**老实说，对个人来说这就是"信任成本"**：先交出卡，才拿到"免费"。
风险不是零 —— 误点升级、账号被盗、用了 Marketplace 里的第三方收费商品都会产生费用。
所以上面那三条保险不是多余的。

**然后别忘了**：就算租户和应用都搞定了，第 ④ 步（Minecraft API 权限白名单）
还在等着 —— 但那是**填表等审核**，不是付费、也不是要做游戏（见上面 ④ 的说明）。

### 客户端 ID 从哪来

**必须是自己的。** PCL CE 的把 client id 编译在密钥里（`Secrets.MSOAuthClientId`），
公开仓库里没有 —— 用别人的等于冒用别人的应用，出问题也是别人被封。
所以这个程序**不内置任何 client id**，界面上填、存在配置里。

### 账户存在哪

`core/accounts.py` → 配置目录的 `accounts.json`。存了名字、UUID、
access/refresh token、过期时间、皮肤地址。

> 🔒 `refresh_token` 等价于**长期的账号访问权**。现在**是明文 json**，
> 界面上**只显示名字和令牌状态**，绝不显示令牌本身。
> 这个文件不要提交到 git、不要发给别人。
> （Windows 上可以上 DPAPI 绑用户，以后要做再说。）

界面上「账户」页能看到：有哪些账号、哪个是当前的、令牌过期没、头像、
以及删除。**过期不等于没用** —— 能拿 refresh_token 刷。

---

## 二、外置登录（authlib-injector / Yggdrasil）

### 原理

`authlib-injector` 是一套**替身协议**：它把游戏客户端里的 Mojang 验证请求
劫持到一个第三方站点上。于是"外置登录站"（皮肤站/验证站）能自己发账号，
玩家用站点的账号登录，照样能进开了外置登录的服务器。

它对外就是一个 **Yggdrasil API**（Mojang 老版验证接口的形状）：

```
<API 根>/
    authserver/authenticate      登录
    authserver/validate          校验令牌
    authserver/refresh           刷新令牌
    sessionserver/session/minecraft/profile/<uuid>   查档案（含皮肤）
    api/profiles/minecraft       按名字批量查档案
    textures/<hash>              皮肤本体
```

### 自动识别（就是"启动器会自动识别"那件事）

用户**只填站点地址**（`demo.lunch.ink`），不用知道 API 根在哪。启动器发一个请求，
读响应头：

```
X-Authlib-Injector-API-Location: /api/yggdrasil/
```

⚠️ 这个值**可能是相对路径**（实测 demo.lunch.ink 给的就是 `/api/yggdrasil/`，
带尾斜杠），所以必须用 `urljoin` 按**请求的那个 URL** 拼，不能当绝对地址用。
它也可能是完整 URL（跨域部署）。两种都认。用户自己填了带路径的地址就尊重他填的。

### 实测 `demo.lunch.ink`（`mc-yggdrasil` 1.0.0）

| 项 | 结果 |
|---|---|
| `GET /` | 200 + `X-Authlib-Injector-API-Location: /api/yggdrasil/` ✅ |
| API 根 | `serverName: "MC Resource Station"`、`skinDomains: [demo.lunch.ink, .demo.lunch.ink]`、`feature.non_email_login: true` |
| `POST /authserver/authenticate` | 403 `"Invalid credentials. Invalid username or password."` ✅ 在 |
| `POST /authserver/validate` / `refresh` | 403 `"Invalid token."` ✅ 在 |
| `POST /api/profiles/minecraft` | 200，未知名字返回**空数组**（不是错误） |
| `GET /sessionserver/.../profile/<uuid>` | 未知 UUID 返回 **204**（不是 404） |
| `/api/users`、`/api/user/profile`、`/api/meta` | 404 —— **没有公开的"列出所有玩家"接口** |

因为最后一条，**外置登录这条路是按名字查，不是列玩家**。用户说的
"玩家账号ID是在皮肤站配置的"就是这个意思：ID 是那边定的，这边按它查。

### 真站点验证记录（`kongxia_114`）

站点给角色发了 `3224f1a59be543dda10092556ae9ae08`，实测：

```
lookup_profiles(["kongxia_114"])
  → [{'id': '3224f1a59be543dda10092556ae9ae08', 'name': 'kongxia_114'}]
fetch_profile(id)
  → properties[textures] base64 解码:
    {"timestamp":..., "profileId":"3224...", "profileName":"kongxia_114", "textures":{}}
                                                              ↑ 空的
```

`textures` 是 `{}` —— **这个角色还没上传皮肤**（站点页面上那两个
「上传皮肤 / 上传披风」按钮就是这个）。所以：
  · 皮肤地址为空 → 头像退回**按 UUID 挑的本地默认皮肤**（和游戏里一致）
  · 结果里明确写"这个角色还没上传皮肤"，不然用户会以为头像加载失败了

⚠️ 外置登录角色**没皮肤时不要**去问 mc-heads 这类皮肤站 —— 它们只认 Mojang 账号，
第三方 UUID 一律不知道，白跑一次网络。直接走本地默认皮肤。

### 站点上那两个按钮复制的东西，两种都能直接粘

| 按钮 | 复制出来大概是 | 我们怎么处理 |
|---|---|---|
| 「复制地址（推荐）」 | `https://demo.lunch.ink` | 读响应头自动识别出 API 根 |
| 「复制完整 API」 | `https://demo.lunch.ink/api/yggdrasil` | 地址里已带路径 → 直接用，不再猜 |

**密码可以留空**：留空就只查角色在不在，不做登录。

### 这个实现的两处"不标准"（必须兼容）

**① 外面套了一层信封。** 规范里 `POST /api/profiles/minecraft` 返回**裸数组**；
这里返回 `{"success": true, "data": [...], "error": null, "traceId": ...}`。
API 根的元数据也是既在顶层又在 `data` 里各放一份。
统一走 `_unwrap()`：是数组就用数组，是信封就掏 `data`。
**没有这一层的话，服务端明明有数据也会被当成空。**

**② 错误体的 `error` 是对象不是字符串。** 规范里是
`{"error": "ForbiddenOperationException", "errorMessage": "..."}`，
这里是 `{"error": {"code": ..., "message": ...}, "errorMessage": "..."}`。
取错误信息一律优先读 `errorMessage`。

### 两种模式

**查名字**（不用密码）—— `POST /api/profiles/minecraft`，查得到就是存在。

**登录**（要密码）—— `POST /authserver/authenticate` → accessToken + selectedProfile。
这才**证明是你的**。而且 `feature.non_email_login` 为真时，用户名不是邮箱。

> 🔒 **密码只在 `core/verify/yggdrasil.py` 这一层出现。**
> 不写配置、不进日志、不留在结果里（`selftest_ali.py` 有一条断言专门查这个）。
> 切换验证方式时会 `clear()` 掉。

### 头像：这才是"真皮肤"

ALI 站点给的档案里带 `textures` 属性（**base64 过的 JSON**），里面有皮肤地址：

```
profile.properties[name=="textures"].value
  → base64 解码 → {"textures": {"SKIN": {"url": "https://.../textures/abc"}}}
```

拿到 URL 之后下载 64×64 的皮肤，用主项目那份 `ui/avatar.py` 的 `face_pixmap()`
抠出 (8,8) 的脸、叠上 (40,8) 的帽子层、最近邻放大、切圆角。

**这比皮肤站的通用头像准** —— 那是真皮肤。所以 PlayerCard 里皮肤地址优先。

⚠️ **皮肤地址必须校验域名在 `skinDomains` 里。** ALI 的签名机制靠它限定
"皮肤只能从这些域加载"，不校验等于允许任意第三方 URL 冒充皮肤。

---

## 三、玩家列表（古法）

### 原理

| | 进服时服务端做什么 | 这个方法有没有意义 |
|---|---|---|
| `online-mode=true`（正版服） | 拿 session 去 **Mojang/微软** 校验，**不过直接踢** | **有** |
| `online-mode=false`（离线服） | 不看，随便填名字都能进 | **没有** |

所以"你能不能出现在**正版服**的在线名单里"，本身就是一次正版验证 ——
验证是服务端做的，启动器只是看一眼名单。

### 四个状态（`inconclusive` 不能省）

| 状态 | 什么情况 | 结论 |
|---|---|---|
| `verified` 通过 | 名字在名单里 | 服务端认了这个账号 |
| `absent` 未通过 | 拿到**完整**名单，没有你 | 要么没进服，要么不是正版 |
| `inconclusive` **无法判定** | 只有**抽样**名单，没抽到你 | **什么都不能说明** |
| `error` 查询失败 | 连不上/超时/协议不对 | —— |

SLP 最多只给 **12 个**名字（原版行为），人多了你本来就可能不在抽样里。
把"没抽到"当"不是正版"会冤枉人。Query 能给完整名单但要服务端开 `enable-query`，
所以顺序是 **先 Query，不通再退回 SLP**。

实测大服：Hypixel 18611/200000 人，SLP 抽样 **0 个**（抽样位被当广告位了）——
所以每条都要**校验 UUID 合法性**，把广告滤掉。

---

## 代码结构

```
main.py                     入口（含 app.setStyle("Fusion")）
core/
    app_info.py             应用名 / 版本 / 配置目录名
    config.py               配置读写（精简版，刻意不复制主项目那份）
    accounts.py             **本地账户存储**（accounts.json）
    logbook.py              **运行日志 + 令牌脱敏**（redact 是唯一入口）
    httplog.py              **带日志的 HTTP 调用**（所有请求都从这走）
    resources.py            ← 从主项目复制：资源路径 + 样式拼接
    theme.py                ← 从主项目复制：调色板 + @变量@ 替换
    verify/                 **验证后端（分模块，和界面无关）**
        base.py             Verifier 接口 + VerifyResult + 四态定义
        __init__.py         注册表（加新方式只改这里）
        microsoft_verifier.py  微软登录验证器（交互式）
        microsoft.py        **微软六步链路**（设备码/Xbox/XSTS/Minecraft）
        thirdparty.py       外置登录验证器
        yggdrasil.py        authlib-injector / Yggdrasil 协议
        player_list.py      古法：查在线名单
        mcping.py           SLP / Query 协议（纯协议，可单独跑）
        srv.py              DNS SRV 查询（自己拼 DNS 包，不引依赖）
        selftest.py         SLP/Query 离线自测
        selftest_ali.py     假 ALI 服务器 + 外置登录离线自测
        selftest_ms.py      假微软/Xbox/Minecraft + 账户存储离线自测
ui/
    avatar.py               ← 从主项目复制：皮肤 → 头像（抠脸 + 帽子层）
    avatar_remote.py        头像/皮肤下载（内存/磁盘/皮肤站/本地 四层兜底）
    icons.py                screen_dpr
    main_window.py          空壳窗口 + 样式加载
    pages/
        verify_page.py      验证页（三套方式的入口）
        accounts_page.py    **账户页**（本地存下来的账号）
        log_page.py         **运行日志页**（实时、可过滤、可复制）
        placeholder_page.py 还没做的页面
    widgets/
        sidebar.py          侧边栏
        player_card.py      玩家卡片
        device_login.py     **微软设备码面板**（交互式登录）
    workers/
        verify_worker.py    验证的后台线程（带取消和令牌）
assets/
    styles/                 ← 从主项目复制（app.qss + parts/）
        parts/80-verify.qss **本实验自己加的**（用同一套 @变量@）
    icons/                  ← 从主项目复制
tools/
    smoke_test.py           界面结构与状态渲染
    e2e_test.py             整条链路
```

### 加一种验证方式

写个 `Verifier` 子类 → 在 `core/verify/__init__.py` 的 `VERIFIERS` 里加一行。
界面自动出现那个按钮、自动按 `needs_server` / `needs_player_name` /
`needs_password` / `needs_client_id` 决定显示哪些输入框。
如果是"要等用户去浏览器操作"的那种，把 `interactive = True` 打开，
界面会换成设备码面板那套 —— **一行界面代码都不用改**。

## 故意的取舍

**配置目录独立**：`%APPDATA%\MosslightVerifyTest`，不和主项目的 `Mosslight` 混，
免得读写配置污染主程序。

**协议层不引第三方库。** `mcping.py` / `srv.py` 只用标准库 `socket`
（Python 没有 SRV 查询，但 DNS 包自己拼也就几十行）。`yggdrasil.py` 用 `requests`。

**必须 `app.setStyle("Fusion")`。** Windows 上 Qt 默认走原生样式（这台机器是
`windows11`）。原生样式会**自己画**按钮斜面、下拉箭头、勾选框方块，
和深色 QSS 打架而且**不报错**。`smoke_test.py` 有断言防它被删。
（离屏下量不出差别：Fusion 和 windows11 **逐像素 0% 差异**，得在真窗口里看。）

**头像四层兜底**：内存 → 磁盘 → （皮肤站 | ALI 皮肤）→ 本地 9 张默认皮肤。
皮肤站挂了只影响头像像不像，不影响能不能用。

**没做**：一键启动游戏进验证服、验证结果持久化、多账号、令牌刷新续期。

## 已知限制

- **微软登录整条链没有在真链路上跑过。** 缺的是"已获批的 Azure 客户端 ID"
  ——没有它，第 4 步一定 403，前面几步再对也走不完。
  所以这一条是用**假微软 + 假 Xbox + 假 Minecraft**验的（`selftest_ms.py`，
  44 项，含 XErr 错误码表、403 应用未授权、没买游戏、refresh_token 轮换）。
  拿到获批的 client_id 之后，改一下配置就能直接用；
  如果哪一步和真实响应不一样，改 `core/verify/microsoft.py` 对应那个函数即可。
- **`accounts.json` 是明文。** refresh_token 等价于长期账号权限。
  界面上不显示令牌，但文件本身没加密（Windows 上可以上 DPAPI，没做）。
- **外置登录不是正版验证。** 它证明"你拥有某验证站上的某个角色"，
  对认这台站的服务器够了，跟 Mojang 没关系。
- **外置登录站没有"列出所有玩家"的接口**（实测 `/api/users` 等全是 404），
  只能按名字查。
- **SLP 抽样只有 12 个。** 大服上"玩家列表"这条路基本没法用，
  它适合"自己搭验证服"或"人少的服"。
- **Query 默认关。** 很多服务器不开，那就只能拿到抽样。
- **SRV 查询要能出网到 53 端口。** 有些网络环境屏蔽公共 DNS，
  这时会退回默认端口 25565（拿不到 SRV 是**正常情况**，不报错）。
- **没有"离线服检测"。** 如果验证服自己就是 `online-mode=false`，
  "玩家列表"得出的"通过"是假的。现在只在界面上写了警告。
- **头像来自第三方**（皮肤站 / 验证站 / Mojang），可用性和限速不归我们管。

## 踩过的坑

1. **`except Exception` 吞掉 ImportError** —— 主项目里那个"默认游戏目录从来没生效"
   就是这么来的。`core/config.py` 读配置只 catch `OSError` / `ValueError`。

2. **Query 协议偏移量**。应答是 `FE FD` + 类型 + 4 字节 session + 正文，
   第一版按固定偏移 `data[16:]` 解析，`hostname` / `numplayers` 全是 None ——
   **看着像"服务器没开 query"，其实是自己错了**。现在先判魔数在不在，
   再按 `类型(1) + session(4) + 正文` 拆。

3. **DNS 压缩指针**。应答里的域名可能是 `0xC0` 开头的压缩指针，不解压读出来是乱码。
   而且"返回给调用方的新偏移"必须是**跳过指针之后**的位置。

4. **`QByteArray` 的悬空引用**。`QBuffer(QByteArray())` 里那个临时对象会被 GC，
   QBuffer 就指向已释放内存 —— 进程直接 `0xC0000409` 崩掉，
   **没有任何 Python 异常可看**。必须留一个引用。

5. **只 connect 信号没发请求**。`PlayerCard` 第一版只连了头像下载器的信号、
   忘了调 `request()`，表现成"头像永远停在首字母占位"而且不报错。
   当时的 e2e 断言写成"断网时允许没有头像"，正好把它放过去了 ——
   **断言太宽松等于没有断言。**

6. **`self.server` 不是你的包装对象**。`BaseHTTPRequestHandler` 里的
   `self.server` 是 `ThreadingHTTPServer` 本身。要用的东西得挂到 httpd 上，
   否则假服务器里 `self.server.base_url` 直接 AttributeError。

7. **204 不是 404**。ALI 的 `/sessionserver/.../profile/<uuid>` 对未知 UUID
   返回 **204 无内容**。按 404 处理的话会把"这个人不在这台站上"当成"服务器坏了"。

8. **改 objectName 之后要 unpolish + polish**。Qt 不会因为名字变了就重新算样式。

9. **布局末尾的撑开项**。列表容器末尾常驻 `addStretch()`，
   卡片必须 `insertWidget(count()-1, ...)` 插在它**前面**。
   少了撑开项，内容不满一屏时多出来的高度会被平摊给每一行。

10. **不设 Fusion 的话自定义 QSS 会漏**。必须在建控件之前 `app.setStyle("Fusion")`。

11. **控制台编码**。中文 Windows 控制台是 GBK，打服务器 MOTD（有 emoji）
    会 `UnicodeEncodeError`。而这个 print 往往写在异常分支里，
    一抛就把"报个错"变成"崩掉"。启动时统一 `reconfigure(errors="replace")`。

12. **`host` 字段有两种形态，不能无条件拼 `:端口`**。玩家列表那条路的
    `host` 是主机名（要拼端口），外置登录那条路是**完整的 API 根 URL**。
    无条件拼的话详情行会显示成 `https://demo.lunch.ink/api/yggdrasil:0`
    —— 实测在真站点上跑出来就是这个鬼样子。现在按"是不是以 http 开头"分流，
    `smoke_test.py` 有断言防回归。

13. **设备码轮询里"还没授权"不是错误。** 服务端返回
    `{"error": "authorization_pending"}`（以及 `slow_down`）是**正常的中间状态**，
    用户还没在浏览器里输完码而已。当成失败的话，用户一点「开始登录」
    就立刻看到"登录失败" —— 这是设备码流程最容易写错的地方。
    代码里它是个单独的异常 `AuthorizationPending`，和 `MicrosoftAuthError` 分开，
    就是为了逼调用方区分这两件事。

14. **`refresh_token` 会轮换。** 微软换一次令牌给一个新的 refresh_token，
    旧的立刻作废。**没把新的存回去 = 下次就用不了了**，而且错误信息是
    `invalid_grant`，看起来像"账号被吊销了"。`core/accounts.py` 的 `upsert`
    特意做了"新的没带就保留旧的"，两个方向都防住了。
    （测试里的假服务器**必须真的轮换**才测得到这条 —— 第一版假服务器
    永远认旧的，测试自己骗自己。）

15. **"能登录" ≠ "有正版"。** 微软那条链的前四步**任何普通微软账号都能走通**，
    没买游戏的账号一样能拿到 Minecraft access_token。只有第 5 步
    （`entitlements/mcstore`）才真正检查所有权。少查这一步的话，
    这个功能会变成一个"谁都能过"的假验证。

16. **`RpsTicket` 前面的 `d=` 不能少。** Xbox Live 那一步要求
    `"RpsTicket": "d=<access token>"`，少了 `d=` 会以很难懂的方式报错。
    这个前缀是代码加的，所以**没法用"传一个没前缀的串"来测** ——
    该验的是"前缀确实加上了"（假服务器会在缺前缀时拒掉）。

17. **"禁用按钮 + 只给 tooltip" = 用户点了没反应。**
    第一版是"没填 client_id 就 `setEnabled(False)`，再用 tooltip 说明原因"。
    问题是 **Windows 上禁用的按钮不弹 tooltip** —— 用户看到的就是
    "点开始没反应"，而且完全不知道缺什么。
    现在改成**按钮一直能点**，缺东西就在状态栏里说清楚缺什么。
    （这条是用户报上来的："现在点开始貌似没有用"。）

18. **失败之后按钮永久变灰，再也不能重试。**
    原因很隐蔽：`_sync_client_state()` 里判断 `worker.isRunning()`，
    而 `failed` 信号是在 `run()` **返回前后**那一刻发的 ——
    主线程处理它的时候线程还没完全结束，`isRunning()` 仍是 True，
    于是跳过了"重新启用按钮"，**而之后再没有任何时机去启用它**。
    现在做了两层：显式的 `_set_busy()` 状态机 + 把 `QThread.finished`
    也接上兜底。防回归断言写在 `smoke_test.py` 的 2c 段。

19. **一个 OAuth 错误码对应好几种真实原因，要先看 AADSTS 码。**
    实测"客户端 ID 根本不存在"和"应用没开公共客户端流"**都返回
    `unauthorized_client`**，只有 `error_description` 里的 AADSTS 码能分清：
    `AADSTS700038` = 这个 ID 在微软那边不存在（抄错了），
    `AADSTS700016` = 当前目录里找不到这个应用。
    只看 OAuth 的 `error` 字段会把"抄错了 ID"说成"去开公共客户端流"，
    用户白折腾一轮。

20. **脱敏的关键词太宽会把正常信息一起遮掉。**
    第一版 `SECRET_HINTS` 里放了 `auth`，结果 `author` 字段（搜索结果里到处都是）
    全变成 `***`。改成只认完整的 `authorization`。
    同理 `code` 也不能当关键词 —— 错误响应里的 `code` 是错误码（有用），
    而真正的授权码出现在 **URL 查询串**里，那是另一套规则（`redact_url()`）。
    **脱敏要"宁可多遮"，但不能遮到把日志变没用** —— 那等于没记。

21. **URL 里的秘密要单独处理。** `redact()` 是按 JSON 的 key 匹配的，
    管不到 `?code=...` 这种查询参数。而 OAuth 的授权码恰好就是这么传的
    （用户第一条消息给的链接就长这样：`.../callback?code=M.C511_...`）。
    另外 `urlencode` 不加 `safe="*"` 会把 `***` 编码成 `%2A%2A%2A`，
    日志里看着像乱码，认不出"这里被遮了"。

22. **日志是从后台线程来的，必须走信号。**
    `logbook` 会被验证用的 QThread、头像线程池回调。直接在回调里
    `appendHtml` 就是**跨线程操作 Qt 控件** —— 表现是时好时坏、偶尔崩，
    最难查的那种。`LogPage.entry_added = pyqtSignal(object)` 让 Qt
    自动排队到主线程。同理页面销毁前一定要**退订**。

23. **只记响应体会漏掉关键信息。** authlib-injector 的自动识别靠的是
    **响应头** `X-Authlib-Injector-API-Location`，它不在 body 里。
    只记 body 的话，"自动识别失败"在日志里是一片空白，根本没法查。
    现在 `httplog.call()` 会把几个有价值的响应头拼进那一行的摘要里。
