FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /src
COPY server/DbFlow.Server.csproj server/packages.lock.json ./
RUN dotnet restore --locked-mode
COPY server/*.cs server/tools.json ./
RUN dotnet publish -c Release --no-restore --self-contained false -p:UseAppHost=false -o /out

FROM mcr.microsoft.com/dotnet/aspnet:8.0
WORKDIR /app
COPY --from=build /out .
RUN mkdir -p /data /config && chgrp -R 0 /data /config /app && chmod -R g=u /data /config /app
ENV ASPNETCORE_HTTP_PORTS=8080 DBFLOW_DATA=/data DBFLOW_CONFIG=/config/server-config.json DOTNET_EnableDiagnostics=0
EXPOSE 8080
USER 1001
ENTRYPOINT ["dotnet", "DbFlow.Server.dll"]
