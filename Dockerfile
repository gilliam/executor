FROM ubuntu:12.04
MAINTAINER Johan Rydberg <johan.rydberg@gmail.com>
RUN echo "force-unsafe-io" > /etc/dpkg/dpkg.cfg.d/02apt-speedup
RUN echo "Acquire::http {No-Cache=True;};" > /etc/apt/apt.conf.d/no-cache
RUN sed 's/main$/main universe/' -i /etc/apt/sources.list
RUN apt-get update && apt-get install -y python python-dev python-pip build-essential libevent-dev git
ADD . /app
WORKDIR /app
RUN pip install -r requirements.txt
EXPOSE 9000:9000
EXPOSE 9001:9001
ENV PYTHONPATH /app
ENTRYPOINT ["bin/executor"]
