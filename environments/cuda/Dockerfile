FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d AS build
RUN pip install --no-cache-dir uv==0.12.1
WORKDIR /opt/campus
COPY sdk/python sdk/python
COPY workloads workloads
ENV UV_PROJECT_ENVIRONMENT=/opt/campus/.venv
RUN uv sync --project workloads --frozen --extra cuda --no-dev --no-editable

FROM python:3.11.14-slim-bookworm@sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
LABEL io.campus.guard="1" io.campus.accelerator="cuda"
COPY --from=build /opt/campus/.venv /opt/campus/.venv
COPY sdk/python/campus /opt/campus/sdk/python/campus
COPY workloads /opt/campus/workloads
COPY environments/guard /opt/campus/environments/guard
COPY environments/offline.py /opt/campus/environments/offline.py
COPY environments/wheelhouse /opt/campus/wheelhouse
ENV PATH=/opt/campus/.venv/bin:/usr/local/bin:/usr/bin:/bin PYTHONPATH=/opt/campus:/opt/campus/sdk/python PYTHONDONTWRITEBYTECODE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
WORKDIR /workspace
ENTRYPOINT ["/opt/campus/.venv/bin/python", "-m", "environments.guard.guard"]
