#!/bin/bash

DEFAULT_PORT=8000
E_WRONGARGS=65 # Bad argument error
E_SUCCESS=0

if [ $# -le 2 ] ; then
    case "$1" in
      stop)
        killall python > /dev/null 2>&1
        exit $E_SUCCESS ;;
      start)
        INST_NB=1 # start one instance if the number of instances is not given
        if [ $# -eq 2 ] ; then
          INST_NB=$2
        fi ;;
      show)
        pgrep python
        exit $E_SUCCESS ;;
      *)
        echo -e "Usage\n\t$0 {start|stop|show}"
        echo -e "Use\n\tstart <N> to start N instances\n\tstop to kill all instances\n\tshow to show all instances"
        exit $E_WRONGARGS ;;
    esac
fi

# stop previous instances, if any
$0 stop
# start N instances
for i in $(seq 0 $((INST_NB-1))) ; do
  nohup ./websoftphone.py $i > /dev/null 2>&1 &
  port=$((DEFAULT_PORT + i))
  echo "Started instance $i on port $port"
done
echo "Done"
