FROM golang:1.24.13-bookworm AS build
WORKDIR /src
ENV GOTOOLCHAIN=local
COPY go.mod go.sum ./
RUN go mod download
COPY main.go http.go store.go ./
RUN CGO_ENABLED=0 go build -mod=readonly -trimpath -o /out/loan-statistics .

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /out/loan-statistics /usr/local/bin/loan-statistics
USER 65532:65532
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/loan-statistics"]
