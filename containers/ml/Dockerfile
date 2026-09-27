# syntax=docker/dockerfile:1.7

ARG PYTHON_BASE=python:3.12.11-slim-bookworm@sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7

FROM ${PYTHON_BASE} AS torch-runtime

ARG UV_VERSION=0.9.7
ARG TORCH_WHEEL_URL=https://download-r2.pytorch.org/whl/cu121/torch-2.5.1%2Bcu121-cp312-cp312-linux_x86_64.whl#sha256=222be02548c2e74a21a8fbc8e5b8d2eef9f9faee865d70385d2eb1b9aabcbc76

ENV VIRTUAL_ENV=/opt/venv \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    UV_LINK_MODE=copy \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NNPACK_DISABLE=1 \
    MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    NUMEXPR_NUM_THREADS=1 \
    PATH=/opt/venv/bin:$PATH

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv venv --python /usr/local/bin/python /opt/venv \
    && uv pip install --python /opt/venv/bin/python --no-cache \
        "${TORCH_WHEEL_URL}" \
        "nvidia-nvjitlink-cu12==12.1.105" \
    && groupadd --gid 10001 ml \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin ml \
    && install -d -o 10001 -g 10001 /opt/app /opt/app/artifacts \
    && install -d -o 10001 -g 10001 \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-root \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-branch-a-primary \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-branch-a-replacement \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-branch-b \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-branch-c \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-leaf-a1 \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-leaf-a2 \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-leaf-b1 \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-leaf-b2 \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-leaf-c1 \
        /var/lib/5g-nwdaf-infrastructure/pymtlf-leaf-c2

FROM torch-runtime AS pymtlf

ARG COMPONENT_REVISION=unknown
ENV PYTHONPATH=/opt/app/src
WORKDIR /opt/app
COPY ML/PyMTLF/pyproject.toml ML/PyMTLF/uv.lock ./
RUN uv export --quiet --frozen --no-dev --no-emit-project --format requirements-txt \
        --output-file /tmp/pymtlf-requirements.txt \
    && uv pip install --python /opt/venv/bin/python --no-cache \
        --requirement /tmp/pymtlf-requirements.txt \
    && rm /tmp/pymtlf-requirements.txt
COPY ML/PyMTLF/run.py ./run.py
COPY ML/PyMTLF/src ./src
COPY ML/PyMTLF/tools/import_seed_model.py ./tools/import_seed_model.py
COPY ML/PyMTLF/tools/evaluate_image_model.py ./tools/evaluate_image_model.py
COPY ML/PyMTLF/seed_models ./seed_models
COPY --chmod=0755 containers/ml/pymtlf-entrypoint.sh ./pymtlf-entrypoint.sh

LABEL org.opencontainers.image.title="5G NWDAF PyMTLF backend" \
      org.opencontainers.image.source="https://github.com/Intelligent-Systems-Lab/PyMTLF" \
      org.opencontainers.image.revision="${COMPONENT_REVISION}"

USER 10001:10001
ENTRYPOINT ["/opt/app/pymtlf-entrypoint.sh"]
CMD ["python", "run.py", "--config", "/etc/5g-nwdaf/config.yaml"]
