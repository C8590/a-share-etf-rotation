# 云服务器部署

本文说明如何把 `a-share-etf-rotation` 部署到一台 Linux 云服务器上，让 Streamlit 网页面板长期在线，并通过定时任务更新数据和生成信号。

## 部署目标

- 让网页面板长期在线。
- 手机和电脑都可以访问面板。
- 只用于查看信号、复盘和人工观察。
- 不自动交易。
- 不连接券商 API。

## 推荐服务器

- 起步配置：2 核 2G。
- 更稳配置：2 核 4G。
- 系统版本：Ubuntu 22.04 或 Ubuntu 24.04。

## 部署步骤

### 1. 安装基础依赖

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

### 2. 克隆仓库

```bash
cd /opt
sudo git clone https://github.com/C8590/a-share-etf-rotation.git
sudo chown -R ubuntu:ubuntu /opt/a-share-etf-rotation
cd /opt/a-share-etf-rotation
```

如果你使用的服务器用户不是 `ubuntu`，请把命令里的 `ubuntu` 替换为实际用户名。

### 3. 初始化 Python 环境

```bash
bash scripts/deploy_linux.sh
```

该脚本会：

- 检查 `python3`、`python3-venv`、`git`。
- 创建或复用 `.venv`。
- 安装 `requirements.txt`。
- 运行一次 `qa-check`。
- 运行一次 `compare-signal`。
- 不创建或覆盖 `config/current_position.yaml`。

### 4. 测试 Streamlit

```bash
bash scripts/run_streamlit.sh
```

测试阶段可以在云服务器安全组或防火墙里临时开放 `8501` 端口，然后访问：

```text
http://服务器IP:8501
```

确认可以访问后，按 `Ctrl+C` 停止前台进程，再配置 systemd 后台常驻。

## systemd 后台常驻

复制服务模板：

```bash
sudo cp deploy/a-share-etf-rotation.service.example /etc/systemd/system/a-share-etf-rotation.service
```

编辑服务文件：

```bash
sudo nano /etc/systemd/system/a-share-etf-rotation.service
```

需要确认或修改：

- `WorkingDirectory=/opt/a-share-etf-rotation`
- `ExecStart=/opt/a-share-etf-rotation/.venv/bin/python -m streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true`
- `User=ubuntu`

如果项目路径或服务器用户名不同，请替换成真实值。

加载并启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable a-share-etf-rotation
sudo systemctl start a-share-etf-rotation
sudo systemctl status a-share-etf-rotation
```

查看日志：

```bash
journalctl -u a-share-etf-rotation -f
```

## 防火墙说明

测试阶段可以临时开放 `8501`：

```bash
sudo ufw allow 8501/tcp
```

正式使用建议通过 Nginx 反向代理到 `80/443`，不建议长期把 `8501` 直接暴露到公网。

如果使用云厂商安全组，也需要在云控制台开放对应端口。

## 定时更新信号

项目提供定时更新脚本：

```bash
bash scripts/update_signal.sh
```

该脚本会依次执行：

```bash
.venv/bin/python main.py update-data
.venv/bin/python main.py qa-check
.venv/bin/python main.py compare-signal
```

日志会写入：

```text
logs/update_signal.log
```

查看日志：

```bash
tail -f logs/update_signal.log
```

配置 cron：

```bash
crontab -e
```

可以参考：

```bash
30 15 * * * /opt/a-share-etf-rotation/scripts/update_signal.sh
40 15 * * 5 /opt/a-share-etf-rotation/scripts/update_signal.sh
```

A 股 15:00 收盘，建议在收盘后留出一点数据更新时间，再运行定时任务。

## 可选 Nginx 反向代理

如果有域名，可以参考：

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/a-share-etf-rotation
sudo ln -s /etc/nginx/sites-available/a-share-etf-rotation /etc/nginx/sites-enabled/a-share-etf-rotation
sudo nginx -t
sudo systemctl reload nginx
```

使用前请把 `server_name your-domain.com;` 替换为你的域名。

如果暂时没有域名，可以先不用 Nginx，直接访问 `http://服务器IP:8501`。

正式使用建议增加 Basic Auth、VPN、白名单或其他访问控制；如果要使用 HTTPS，建议再配置证书。

## 安全边界

- 本项目不自动下单。
- 本项目不连接券商 API。
- 云部署只用于查看信号、复盘和人工观察。
- 不要公开暴露持仓信息。
- 建议使用私有仓库，或者至少不要上传 `config/current_position.yaml`。
- 建议给网页加密码或放在受控网络后面。
- 任何真实交易都应由人工独立确认，并在券商 App 中手动完成。
