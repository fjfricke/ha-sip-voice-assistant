ARG BUILD_FROM=ghcr.io/hassio-addons/base:14
FROM ${BUILD_FROM}

# Set working directory
WORKDIR /app

# Install Python 3.12 and dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-wheel \
    py3-setuptools \
    curl

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 - && \
    ln -s /root/.local/bin/poetry /usr/local/bin/poetry

# Copy Poetry files
COPY pyproject.toml poetry.toml poetry.lock* /app/

# Install dependencies
WORKDIR /app
RUN poetry config virtualenvs.create false && \
    poetry install --no-dev --no-interaction --no-ansi

# Copy application files
COPY app/ /app/app/
COPY config/ /app/config/

# Copy run script
COPY run.sh /run.sh

# Set permissions
RUN chmod a+x /run.sh

# Run the application
CMD ["/run.sh"]