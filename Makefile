.PHONY: install dev install-test test clean run

# Install dependencies
install:
	poetry install

# Install with dev dependencies
dev:
	poetry install --with dev

# Run the application
run:
	poetry run python -m app.main

# Run tests
test:
	poetry run pytest tests/

# Clean up
clean:
	poetry env remove python
	rm -rf .venv
	rm -rf dist/
	rm -rf *.egg-info

# Setup for development
setup: install
	@echo "Virtual environment created in .venv/"
	@echo "Run 'poetry shell' to activate, or use 'poetry run <command>'"

