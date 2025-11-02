# Build argument - Home Assistant Supervisor overrides this with the correct arch
# For local testing, you can use: docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM ${BUILD_FROM}

# Set working directory
WORKDIR /app

# Install Python 3.12 and dependencies
# Note: g711, g722, and samplerate require compilation from source (no pre-built wheels)
# These packages need build tools: C compiler, CMake, and make
RUN apk add --no-cache \
    python3 \
    python3-dev \
    py3-pip \
    py3-wheel \
    py3-setuptools \
    curl \
    git \
    libsamplerate \
    libsamplerate-dev \
    py3-pybind11 \
    build-base \
    cmake

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy Poetry files
COPY pyproject.toml poetry.toml poetry.lock* /app/

# Install dependencies
WORKDIR /app
RUN poetry config virtualenvs.create false && \
    poetry install --only=main --no-interaction --no-ansi --no-root

# Copy application files
COPY app/ /app/app/
COPY config/ /app/config/

# Copy run script
COPY run.sh /run.sh

# Set permissions
RUN chmod a+x /run.sh

# Run the application
CMD ["/run.sh"]