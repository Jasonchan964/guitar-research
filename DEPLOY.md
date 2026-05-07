# 部署到公网（Render · 免费档）

部署完成后会得到 **HTTPS 网址**，任何人用手机或电脑都能打开；与国内是否使用 VPN 无关（是否能访问 Reverb 取决于当地网络到国外的连通性）。

## 你需要准备

1. **GitHub 账号**：把本文件夹 `guitar-search` 打成仓库并推送（代码里不要提交 `.env` / `.env.txt`）。
2. **Render 账号**：[https://render.com](https://render.com) 用 GitHub 登录。
3. **密钥**：在 Render 控制台 **Environment** 里配置（不要写进代码）：
   - **`REVERB_API_TOKEN`**：Reverb Personal Access Token  
   - **`EXCHANGE_RATE_API_KEY`**：[ExchangeRate-API](https://www.exchangerate-api.com/) 密钥（用于 **`/api/exchange-rate`**）

## 用 Blueprint 一键部署（推荐）

1. Render 控制台：**New** → **Blueprint**。
2. 连接你的 GitHub 仓库；若仓库根目录不是 `guitar-search`，请选择包含 `Dockerfile` 的子目录（或在 Blueprint 里指向正确路径）。
3. 应用创建后，打开 **Environment**，新增密钥：
   - **Key**：`REVERB_API_TOKEN`
   - **Value**：你在 Reverb 后台生成的 Personal Access Token  
4. 等待构建结束（首次 Docker 构建约几分钟）。
5. 打开 Render 给你的域名，例如：**`https://guitar-search-xxxx.onrender.com`**（以控制台为准）。

同一域名下既有前端页面，又有 **`/search`** API（无需再配置前端跨域）。

## 免费档说明

- **冷启动**：一段时间没人访问后，第一次打开可能要等几十秒唤醒。
- **额度**：以 Render 当前政策为准。

## 本地验证「单端口网页 + API」（可选）

```powershell
cd guitar-search
npm run build
cd backend
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

浏览器访问：**http://127.0.0.1:8000/** ，搜索应调用同源 **`/search`**。
