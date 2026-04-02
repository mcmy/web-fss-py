# web-fss

`web-fss` is a practical replacement for `python -m http.server`.

## Features

- Directory listing Web UI
- Resume download via HTTP range (`206 Partial Content`)
- Web upload button with chunked upload
- Same-name conflict check before upload
- Conflict options: overwrite / rename / cancel
- Overwrite or resume upload flow
- `.upload` metadata for upload progress
- Auto-remove `.upload` after upload completion
- File and folder delete button in UI
- Open mode switch in UI: `Preview` (browser MIME rendering) / `Download`
- `--serve-index-html` to auto-serve `index.html`/`index.htm` on directory request

Python compatibility: `3.7` to `3.13` (and newer 3.x).

## Install

```bash
pip install web-fss
```

For local development:

```bash
pip install -e .
```

## Run

Default (current directory, port 8000):

```bash
web-fss
```
or
```bash
python -m web_fss
```

Tip (recommended quick run):

```bash
uvx web-fss
```

Custom port and directory:

```bash
web-fss 9000 -d /data/files
```

Bind local only:

```bash
web-fss -b 127.0.0.1
```

Disable upload:

```bash
web-fss --no-upload
```

Show hidden files (dotfiles):

```bash
web-fss --show-hidden
```

Set upload chunk size (default 4MB):

```bash
web-fss --chunk-size 1048576
```

Auto-serve `index.html`/`index.htm` for directory requests:

```bash
web-fss --serve-index-html
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

- `GET /.api/list?directory=/path/`
- `POST /.upload/check`
- `POST /.upload/init`
- `POST /.upload/chunk`
- `POST /.upload/delete`
- `DELETE /.upload/delete`

## License

MIT
