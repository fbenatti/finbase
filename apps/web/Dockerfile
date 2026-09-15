FROM node:20-alpine AS build

WORKDIR /app

COPY apps/web/package.json apps/web/yarn.lock* ./
RUN yarn install --frozen-lockfile

COPY apps/web/ ./
RUN yarn build

FROM nginx:1.27-alpine AS production

COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
