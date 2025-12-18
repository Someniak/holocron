# Contributing to Holocron

Thank you for your interest in contributing to Holocron! We welcome contributions... Please follow below steps to contribute.

## Development Setup

We use `uv` for dependency management and running tasks.

1.  **Install uv**: [Follow instructions here](https://github.com/astral-sh/uv)
2.  **Clone the repo**:
    ```bash
    git clone https://github.com/someniak/holocron.git
    cd holocron
    ```
3.  **Install dependencies**:
    ```bash
    uv sync
    ```

## Running Tests

Run the test suite with `uv`:

```bash
uv run pytest
```

With coverage:
```bash
uv run pytest --cov=src
```
uv run pytest --cov=src
```

## Making Contributions

Please open a Pull Request with your feature branch to `main`. This will be vetted and analyzed by the CI pipeline and the repository owner. 

Please make use of the following branch format: `feat/your-feature-name`.


---

## Code Style

*   We use standard Python styling (PEP 8).
*   Type hints are encouraged.
