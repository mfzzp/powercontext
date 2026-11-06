# 本地部署：Dify + Qwen + PowerContext

本方案使用 Dify 1.13.0 的官方 Compose、本机 llama.cpp CPU 推理，以及实际 PowerContext 记忆服务。无需云端模型额度。默认模型为 Qwen3 1.7B Q8_0，约 1.83 GB；仍存在生成幻觉，见主 README 的实际限制，可在 Dify 中更换更强的聊天模型。

## 首次安装（Linux / WSL Ubuntu）

需要 Python 3.12、Git、Docker Engine、Docker Compose 2.35 以上、libgomp1。建议至少 8 GB 可用内存和 15 GB 磁盘空间。Windows 使用 WSL 2；模型和 Docker 数据保存在 Linux 文件系统。Docker 镜像、源码、模型及插件的首次下载需要网络。

Ubuntu 24.04 可先安装系统依赖；本次实际使用 Ubuntu 更新仓库提供的 Docker 29.1.3 和 Compose 2.40.3：

```bash
sudo apt-get update
sudo apt-get install -y git ca-certificates python3.12-venv libgomp1 docker.io docker-compose-v2
sudo systemctl start docker
docker compose version
```

确认 Compose 不低于 2.35，再执行后续命令。Windows 尚无 Linux 时，应先安装 WSL 2 / Ubuntu 并完成重启；现有本机环境已经安装完成。

在仓库 `dify` 目录执行以下命令。命令中的当前用户须能通过 sudo 管理 Docker 和 systemd。

```bash
export SHOP_HOME="$HOME/.local/share/dify-grudge-shop"
mkdir -p "$SHOP_HOME"
git clone --depth 1 --branch 1.13.0 https://github.com/langgenius/dify.git "$SHOP_HOME/dify-upstream"
python3.12 -m venv "$SHOP_HOME/venv"
"$SHOP_HOME/venv/bin/python" -m pip install -r requirements.txt
"$SHOP_HOME/venv/bin/python" deploy/download_assets.py
mkdir -p "$SHOP_HOME/llama"
tar -xzf "$SHOP_HOME/assets/llama-b12144-bin-ubuntu-x64.tar.gz" -C "$SHOP_HOME/llama"
"$SHOP_HOME/venv/bin/python" deploy/prepare_compose.py
sudo python3 deploy/install_services.py "$(id -un)" "$SHOP_HOME/venv/bin/python"
(cd "$SHOP_HOME/dify-upstream/docker" && sudo docker compose up -d && sudo docker compose restart nginx)
# 等待数据库迁移、模型加载和记忆 HTTP 服务完成；未就绪将非零退出。
python3 deploy/check_ready.py
"$SHOP_HOME/venv/bin/python" build_dsl.py
"$SHOP_HOME/venv/bin/python" deploy/configure_dify.py
```

`configure_dify.py` 为这个专用本机 Dify 创建管理员，安装官方签名插件，验证模型，导入并发布 DSL。再次运行更新同一个应用。凭据只存于 `$SHOP_HOME/credentials.json`，权限 600；不要提交此文件。导入时仅在内存中填入 `NPC_API_TOKEN`，仓库 DSL 保留空值。实际入口写入 `$SHOP_HOME/deployment.json`。

官方模型插件固定为 `langgenius/openai_api_compatible` 0.0.73；插件包 SHA256 在配置脚本中校验。模型、推理运行时来源、固定版本和 SHA256 见 `assets.json`。服务端口：Dify 18080（仅 localhost），记忆服务 8790，模型 8791（均需认证）。本方案供本机和 Docker 容器访问。Dify 的 SSRF 代理只为 `host.docker.internal:8790` 增加本项目访问规则。

默认使用 Qdrant 作为 Dify 官方部署的向量组件，本游戏不创建知识库、不使用向量检索。参赛指定技术的实际作用来自 PowerContext 记忆读写；Qdrant 不替代 PowerContext。

## 验证顺序

使用新的存档码，从空状态验证。两条实际验证命令之间重启记忆服务，以验证 PowerContext 的持久化复用。

```bash
"$SHOP_HOME/venv/bin/python" verify_dify.py --player demo-new-001 --phase first --credentials-file "$SHOP_HOME/credentials.json" --url http://127.0.0.1:18080/v1
sudo systemctl restart dify-grudge-memory
"$SHOP_HOME/venv/bin/python" verify_dify.py --player demo-new-001 --phase return --credentials-file "$SHOP_HOME/credentials.json" --url http://127.0.0.1:18080/v1
"$SHOP_HOME/venv/bin/python" verify_dify.py --player demo-new-001 --phase repair --credentials-file "$SHOP_HOME/credentials.json" --url http://127.0.0.1:18080/v1
"$SHOP_HOME/venv/bin/python" verify_dify.py --player demo-other-001 --phase isolation --credentials-file "$SHOP_HOME/credentials.json" --url http://127.0.0.1:18080/v1
```

预期：第一轮记录咖啡价签恶作剧；服务重启后，第二轮读到第一轮记录且剧情具体回应；第三轮对道歉和恢复价签产生反馈；另一个存档码读取 0 条历史。每轮必须实际 `succeeded` 并有 PowerContext 凭据。人工核对剧情；本机模型仍会虚构细节或旧事，须区分模型台词和实际记忆。运行证据保存在 `dify/evidence/live-*.json`，PowerContext 审计保存在 `$SHOP_HOME/memory/powercontext-audit.jsonl`。这些 API 请求不使用固定模型台词或预计算回复。

将本次四份结果 JSON 复制到一个独立目录后，可导出真实节点执行、脱敏 DSL 和对应审计，并校验读取数量：

```bash
"$SHOP_HOME/venv/bin/python" deploy/export_evidence.py --folder evidence/your-verification-folder
```

已有完整运行材料见 `evidence/verified-20261106-17b/`。导出包含实际模型原文，不会把生成问题改写为通过。

换新的玩家码即可恢复空存档样例，不需要删除其他玩家数据。相同玩家不要并行运行多轮；服务拒绝过时的进度写入。

## 重新启动与排错

Linux：`sudo systemctl start docker dify-grudge-model dify-grudge-memory`，再进入部署目录运行 `sudo docker compose up -d`。Windows 可运行本目录 `Start-Dify.ps1`；默认发行版 Ubuntu-24.04、Linux 用户 minimax，可通过参数修改。

启动脚本现在会等待 Dify API、模型和记忆服务均能返回 HTTP 200，再显示入口。该检查不替代真实工作流验证。WSL 休眠或 Windows 重启后应重新执行启动脚本；不能以 Docker 容器列表显示 Up 判断完整应用已经就绪。脚本通过隐藏的 WSL 会话保持本机服务运行，关闭该会话或执行 `wsl --shutdown` 会停止本地服务。

查看本机服务：`sudo journalctl -u dify-grudge-model -u dify-grudge-memory -n 40`。查看 Dify：在部署目录执行 `sudo docker compose ps`、`sudo docker compose logs --tail 60 api plugin_daemon sandbox`。插件首次安装需下载 Python 依赖。镜像或模型下载中断后重试会利用已有缓存；镜像下载受限时，需为 Docker daemon 配置当前机器确实可用的代理，不能把容器内 localhost 当作 Windows 代理地址。

停止：在部署目录执行 `sudo docker compose stop`，并执行 `sudo systemctl stop dify-grudge-model dify-grudge-memory`。上述操作保留数据库和模型。系统服务不会通过本脚本设置为开机自动启动。

公开部署需自行配置 HTTPS、访问权限和网络范围。本项目不依赖作者已有账号、云端模型 Key 或预置记忆。
