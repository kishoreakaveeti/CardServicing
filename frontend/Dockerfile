FROM node:22-alpine AS build
WORKDIR /ui
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.28-alpine AS runtime
ENV EXPERIENCE_UPSTREAM=experience:8000 DNS_RESOLVER=127.0.0.11
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /ui/dist /usr/share/nginx/html
EXPOSE 8080

FROM build AS debug
CMD ["npm", "run", "dev"]
