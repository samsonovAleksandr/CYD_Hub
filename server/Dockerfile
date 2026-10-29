# Официальный образ Playwright: Ubuntu 24.04 + Chromium с зависимостями
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

ENV PYTHONUNBUFFERED=1
WORKDIR /srv

# ping — для проверок, шрифты — чтобы экран рисовался красиво и с кириллицей
RUN apt-get update \
 && apt-get install -y --no-install-recommends iputils-ping fonts-inter fonts-dejavu-core tzdata curl fontconfig \
 && rm -rf /var/lib/apt/lists/*

# шрифты для тем экрана (все с кириллицей), Google Fonts, лицензия OFL
RUN mkdir -p /usr/share/fonts/truetype/cyd && cd /usr/share/fonts/truetype/cyd \
 && for f in "jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf" "pixelifysans/PixelifySans%5Bwght%5D.ttf" \
             "russoone/RussoOne-Regular.ttf" "exo2/Exo2%5Bwght%5D.ttf" "pressstart2p/PressStart2P-Regular.ttf"; do \
      curl -fsSLO "https://github.com/google/fonts/raw/main/ofl/$f" || echo "нет шрифта $f"; \
    done \
 && fc-cache -f

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8770
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8770", "--no-access-log"]
