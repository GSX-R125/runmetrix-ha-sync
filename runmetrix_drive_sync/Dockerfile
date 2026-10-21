FROM rclone/rclone:1.75.1
RUN apk add --no-cache python3
COPY run.sh /app/run.sh
COPY upload_by_month.py /app/upload_by_month.py
ENTRYPOINT ["/bin/sh", "/app/run.sh"]
