# Grafana with the CCI Plant Monitoring look: the CC logo instead of Grafana's, "CCI Plant Monitoring"
# as the window/browser title, and the SQLite data source plugin built in (no internet needed at start).
# docker-compose.yml builds it; nothing here changes how Grafana reads the data.
FROM grafana/grafana:11.3.0

USER root
ENV GF_PATHS_PLUGINS=/opt/grafana-plugins
RUN mkdir -p /opt/grafana-plugins \
 && grafana cli --pluginsDir /opt/grafana-plugins plugins install frser-sqlite-datasource 4.0.6 \
 && chown -R grafana:root /opt/grafana-plugins

COPY branding/grafana_icon.svg     /usr/share/grafana/public/img/grafana_icon.svg
COPY branding/fav32.png            /usr/share/grafana/public/img/fav32.png
COPY branding/apple-touch-icon.png /usr/share/grafana/public/img/apple-touch-icon.png
COPY branding/cci-logo.png         /usr/share/grafana/public/img/cci-logo.png
RUN find /usr/share/grafana/public/build -name '*.js' -exec sed -i \
      -e 's/AppTitle="Grafana"/AppTitle="CCI Plant Monitoring"/g' \
      -e 's/LoginTitle="Welcome to Grafana"/LoginTitle="CCI Plant Monitoring"/g' {} + \
 && sed -i 's/\[\[\.AppTitle\]\]/CCI Plant Monitoring/g' /usr/share/grafana/public/views/index.html

USER grafana
