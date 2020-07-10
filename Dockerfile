FROM python:3

RUN apt -y update
RUN apt -y install libxml2-utils

COPY run-xmllint-format.py /run-xmllint-format.py
COPY entrypoint.sh /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
