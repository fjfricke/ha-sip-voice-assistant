# Build argument - Home Assistant Supervisor overrides this with the correct arch
# For local testing, you can use: docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM ${BUILD_FROM}

# Set working directory
WORKDIR /app

# Install Python 3.12 and dependencies
# Note: LLVM, cmake, g++, make, and musl-dev are needed for building llvmlite (dependency of numba/resampy)
RUN apk add --no-cache \
    python3 \
    python3-dev \
    py3-pip \
    py3-wheel \
    py3-setuptools \
    curl \
    git \
    cmake \
    make \
    gcc \
    g++ \
    musl-dev \
    linux-headers \
    llvm20 \
    llvm20-dev \
    llvm20-static \
    llvm20-gtest \
    libsamplerate \
    libsamplerate-dev \
    py3-pybind11

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy Poetry files
COPY pyproject.toml poetry.toml poetry.lock* /app/

# Install dependencies
WORKDIR /app
# Set environment variables for build tools (needed for llvmlite compilation)
# These must be set in the RUN command so Poetry's build process can access them
ENV CC=gcc
ENV CXX=g++
ENV CMAKE_MAKE_PROGRAM=make
ENV PATH="/usr/bin:$PATH"
ENV LLVM_DIR=/usr/lib/llvm20/lib/cmake/llvm
RUN CC=gcc CXX=g++ CMAKE_MAKE_PROGRAM=make LLVM_DIR=/usr/lib/llvm20/lib/cmake/llvm PATH="/usr/bin:$PATH" \
    poetry config virtualenvs.create false && \
    CC=gcc CXX=g++ CMAKE_MAKE_PROGRAM=make LLVM_DIR=/usr/lib/llvm20/lib/cmake/llvm PATH="/usr/bin:$PATH" \
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