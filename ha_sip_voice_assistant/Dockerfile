# Build argument - Home Assistant Supervisor overrides this with the correct arch
# For local testing, you can use: docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM ${BUILD_FROM}

# Set working directory
WORKDIR /app

# Install Python 3.12 and dependencies
# Note: g711, g722, and samplerate require compilation from source (no pre-built wheels)
# scipy also requires compilation from source for armv7 (no pre-built wheel)
# These packages need build tools: C compiler, CMake, make, Fortran compiler, and OpenBLAS
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
    gfortran \
    cmake \
    libffi-dev \
    openblas \
    openblas-dev

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy only Poetry dependency files to temp location (for better layer caching)
# This layer will be cached unless these files change
WORKDIR /tmp/build
COPY pyproject.toml poetry.lock* poetry.toml ./

# Install dependencies in temp location (this layer is cached if dependency files don't change)
# Dependencies are installed system-wide (virtualenvs.create false)
RUN poetry config virtualenvs.create false && \
    poetry install --only=main --no-interaction --no-ansi --no-root

# Set working directory and copy all application files after dependencies are installed
WORKDIR /app
COPY app/ /app/app/
COPY config/ /app/config/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Run the application
CMD ["/run.sh"]