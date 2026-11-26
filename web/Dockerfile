FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html styles.css app.js lib.js synthese.js ligne.js planning.js systeme.html systeme-connexion.html /usr/share/nginx/html/
COPY vendor /usr/share/nginx/html/vendor
EXPOSE 8080
