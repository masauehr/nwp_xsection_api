# 断面図API（Render 用）。pygrib は PyPI の wheel（eccodes 同梱）を使う。日本語の図の文字用にIPAフォントだけ入れる。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mpl \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    WORK_DIR=/tmp/nwp_xsection

RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-ipafont-gothic \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py auth.py emagram.py engine.py ghpub.py limits.py nwp_data.py opts.py plotutil.py xsection.py ./
COPY assets ./assets

# 一般ユーザーで動かす（root では動かさない）
RUN useradd -m appuser && mkdir -p /tmp/nwp_xsection /tmp/mpl && chown -R appuser /tmp/nwp_xsection /tmp/mpl
USER appuser

EXPOSE 8800
CMD ["python", "app.py"]
