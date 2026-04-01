# web-fs-server

`web-fs-server` is a practical replacement for `python -m http.server`.

Chinese documentation: [README.zh-CN.md](./README.zh-CN.md)

## Features

- Directory listing Web UI
- Resume download via HTTP range (`206 Partial Content`)
- Web upload button with chunked upload
- Same-name conflict check before upload
- Overwrite or resume upload flow
- `.upload` metadata for upload progress
- Auto-remove `.upload` after upload completion
- File and folder delete button in UI

Python compatibility: `3.7` to `3.13` (and newer 3.x).

## Install

```bash
pip install web-fs-server
```

For local development:

```bash
pip install -e .
```

## Run

Default (current directory, port 8000):

```bash
web-fs-server
```

Tip (recommended quick run):

```bash
uvx web-fs-server
```

Custom port and directory:

```bash
web-fs-server 9000 -d /data/files
```

Bind local only:

```bash
web-fs-server -b 127.0.0.1
```

Disable upload:

```bash
web-fs-server --no-upload
```

Set upload chunk size (default 4MB):

```bash
web-fs-server --chunk-size 1048576
```

## Resumable Upload

When uploading `a.zip`, server creates:

- `a.zip` (target file)
- `a.zip.upload` (JSON metadata)

Metadata fields:

- `file_size`
- `uploaded_ranges` (`[start, end)`)
- `bytes_received`
- `completed`

When uploading same filename again:

1. Check existing file and `.upload`
2. Resume if state is valid
3. Otherwise overwrite
4. Remove `.upload` after completion

## API Endpoints

- `POST /.upload/check`
- `POST /.upload/init`
- `POST /.upload/chunk`
- `POST /.upload/delete`
- `DELETE /.upload/delete`

## Build & Publish (PyPI)

Install publish tools:

```bash
uv pip install -U twine
```

Build package:

```bash
uv build
```

Check package files:

```bash
uvx twine check dist/*.whl dist/*.tar.gz
```

Upload to PyPI:

```bash
uvx twine upload dist/*.whl dist/*.tar.gz
```

Upload to TestPyPI:

```bash
uvx twine upload --repository testpypi dist/*.whl dist/*.tar.gz
```

## License

MIT
