# web-fs-server

`web-fs-server` 是 `python -m http.server` 的实用替代方案。

## 功能

- 目录浏览 Web UI
- 断点下载（HTTP `Range` / `206`）
- 上传按钮 + 分片上传
- 上传前同名冲突检测
- 同名冲突支持：覆盖 / 重命名 / 取消
- 支持覆盖上传/断点续传
- 用 `.upload` 元数据记录上传进度
- 上传完成自动删除 `.upload`
- UI 支持删除文件和文件夹

Python 兼容版本：`3.7` 到 `3.13`（及后续 3.x）。

## 安装

```bash
pip install web-fs-server
```

本地开发安装：

```bash
pip install -e .
```

## 启动

默认（当前目录，端口 8000）：

```bash
web-fs-server
```

提示（推荐快速启动）：

```bash
uvx web-fs-server
```

指定端口和目录：

```bash
web-fs-server 9000 -d /data/files
```

只监听本地：

```bash
web-fs-server -b 127.0.0.1
```

关闭上传：

```bash
web-fs-server --no-upload
```

设置上传分片大小（默认 4MB）：

```bash
web-fs-server --chunk-size 1048576
```

## 续传逻辑

上传 `a.zip` 时会创建：

- `a.zip`（目标文件）
- `a.zip.upload`（JSON 元数据）

元数据字段：

- `file_size`
- `uploaded_ranges`（`[start, end)`）
- `bytes_received`
- `completed`

再次上传同名文件时：

1. 检查现有文件与 `.upload`
2. 状态有效则续传
3. 否则覆盖
4. 上传完成删除 `.upload`

## API 接口

- `GET /.api/list?directory=/path/`
- `POST /.upload/check`
- `POST /.upload/init`
- `POST /.upload/chunk`
- `POST /.upload/delete`
- `DELETE /.upload/delete`

## 许可证

MIT
