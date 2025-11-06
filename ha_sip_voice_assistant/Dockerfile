# Build argument - Home Assistant Supervisor overrides this with the correct arch
# For local testing, you can use: docker build --build-arg BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:latest
FROM ${BUILD_FROM}

# Set working directory
WORKDIR /app

# Install Python 3.12 and dependencies
# Note: g711, g722, and samplerate require compilation from source (no pre-built wheels)
# scipy also requires compilation from source for armv7 (no pre-built wheel)
# PJSIP requires compilation from source (pjsua2 Python bindings)
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
    openblas-dev \
    alsa-lib-dev \
    openssl-dev \
    pkgconfig \
    swig

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Build and install PJSIP from source (required before poetry install)
# PJSIP is not available on PyPI, so we must build it manually
# Following the pattern from working projects: clone, configure, make, install Python bindings
WORKDIR /tmp
RUN git clone --depth 1 --branch 2.15.1 https://github.com/pjsip/pjproject.git && \
    cd pjproject && \
    set -xe && \
    ./configure CFLAGS="-O3 -DNDEBUG -fPIC" --enable-shared --enable-python --with-ssl && \
    make && \
    make dep && \
    make install && \
    cd pjsip-apps/src/swig && \
    make python && \
    make -C python install && \
    cd / && \
    rm -rf /tmp/pjproject

# Copy only Poetry dependency files to temp location (for better layer caching)
# This layer will be cached unless these files change
WORKDIR /tmp/build
COPY pyproject.toml poetry.lock* poetry.toml ./

# Install dependencies in temp location (this layer is cached if dependency files don't change)
# Dependencies are installed system-wide (virtualenvs.create false)
# Note: pjsua2 is already installed manually, so we temporarily remove it from pyproject.toml
# and regenerate lock file, then install using the lock file
RUN poetry config virtualenvs.create false && \
    # Verify pjsua2 is installed
    python3 -c "import pjsua2; print('✅ pjsua2 is available')" && \
    # Temporarily remove pjsua2 from dependencies
    sed -i.bak '/^pjsua2 =/d' pyproject.toml && \
    # Regenerate lock file without pjsua2 (this ensures lock file matches pyproject.toml)
    poetry lock --no-interaction && \
    # Install dependencies using the lock file
    poetry install --only=main --no-interaction --no-ansi --no-root && \
    # Restore pyproject.toml
    mv pyproject.toml.bak pyproject.toml 2>/dev/null || true && \
    # Verify all dependencies are installed
    python3 -c "import aiohttp, websockets, yaml, scipy, numpy, pjsua2; print('✅ All dependencies available')"

# Set working directory and copy all application files after dependencies are installed
WORKDIR /app
COPY app/ /app/app/
COPY config/ /app/config/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

# Run the application
CMD ["/run.sh"]