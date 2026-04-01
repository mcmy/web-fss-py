
## Build & Publish (PyPI)

Install publish tools(option uvx):

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