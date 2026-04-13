ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip py3-serial

WORKDIR /app

COPY warema_bridge/requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

COPY warema_bridge/ .

RUN chmod +x run.sh

CMD ["/app/run.sh"]
