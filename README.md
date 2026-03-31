# web-fs-server

`web-fs-server` 是一个可替代 `python -m http.server` 的文件服务工具，支持：

- 目录浏览（Web UI 风格接近 `http.server`）
- 断点下载（HTTP `Range` / `206 Partial Content`）
- Web 上传按钮 + 分片上传
- 同名文件上传前检测
- 覆盖上传或断点续传（二选一）
- `.upload` 元数据文件（记录文件总大小、已上传分片范围）

兼容 Python 版本：`3.7` 到 `3.13`（以及后续 3.x 版本）。

## 安装

```bash
pip install web-fs-server
```

或本地开发安装：

```bash
pip install -e .
```

## 使用

默认当前目录、端口 `8000`：

```bash
web-fs-server
```

指定目录和端口：

```bash
web-fs-server 9000 -d /data/files
```

仅本机访问：

```bash
web-fs-server -b 127.0.0.1
```

关闭上传功能（只保留浏览 + 下载）：

```bash
web-fs-server --no-upload
```

调整上传分片大小（默认 4MB）：

```bash
web-fs-server --chunk-size 1048576
```

## 续传行为说明

上传文件 `a.zip` 时，服务端会创建：

- `a.zip`（目标文件，按总大小预分配）
- `a.zip.upload`（JSON 元数据）

`.upload` 中包含：

- `file_size`: 目标文件总大小
- `uploaded_ranges`: 已上传分片区间（`[start, end)`）
- `bytes_received`: 已覆盖字节数
- `completed`: 是否完整上传

上传完成后，`a.zip.upload` 会被自动删除，不会长期保留。

当再次上传同名文件时：

1. 先检测同名文件和 `.upload`
2. 若满足续传条件（`file_size` 一致且目标文件大小一致），可选择“续传”
3. 若不满足续传条件，会提示“覆盖或取消”
4. 续传校验采用简单大小一致性判断，避免误把被改动文件当作可续传目标

## HTTP 接口（供前端页面调用）

- `POST /.upload/check`：检查同名与可续传状态
- `POST /.upload/init`：初始化上传会话（`overwrite` / `resume`）
- `POST /.upload/chunk`：上传单个分片

## 发布到 PyPI

先安装构建工具：

```bash
python -m pip install --upgrade build twine
```

构建：

```bash
python -m build
```

上传（正式仓库）：

```bash
twine upload dist/*
```

上传（测试仓库）：

```bash
twine upload --repository testpypi dist/*
```

## 许可证

MIT
