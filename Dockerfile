FROM alpacamarkets/pylivetrader:0.0.25

# Change to 0 to disable the installation of the redis library
ARG USE_REDIS=1
ENV USE_REDIS=$USE_REDIS
RUN bash -c 'if [ "$USE_REDIS" == 1 ] ; then pip install redis ; fi'

RUN git clone --depth 1 https://github.com/trobrock/pipeline-live.git --branch master --single-branch && \
    pip uninstall -y pipeline-live && \
    cd pipeline-live && \
    python setup.py install && \
    cd .. && rm -rf pipeline-live

ARG ALGO
ARG APCA_API_SECRET_KEY
ARG APCA_API_KEY_ID
ARG APCA_API_BASE_URL

ENV ALGO=$ALGO
ENV APCA_API_SECRET_KEY=$APCA_API_SECRET_KEY
ENV APCA_API_KEY_ID=$APCA_API_KEY_ID
ENV APCA_API_BASE_URL=$APCA_API_BASE_URL

RUN mkdir /app

ADD algo /app/algo
ADD tmp /app/tmp
ADD run /app

WORKDIR /app

CMD ./run $ALGO
