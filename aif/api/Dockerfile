FROM golang:1.24-alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 GOOS=linux \
    go build -trimpath -ldflags="-s -w" \
    -o /aif-api .

FROM alpine:3.21

RUN apk add --no-cache ca-certificates \
    && addgroup -S aif \
    && adduser -S aif -G aif

COPY --from=builder /aif-api /usr/local/bin/aif-api

USER aif

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/aif-api"]
