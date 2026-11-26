FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY *.html *.css *.js /usr/share/nginx/html/
COPY vendor /usr/share/nginx/html/vendor
EXPOSE 8080
