#-*- coding:utf-8 -*-
import json

__author__ = 'cc'

from functools import wraps

import redis
import time
import queue
import traceback
from collections import Callable
from concurrent.futures import ThreadPoolExecutor, Future
from concurrent.futures.thread import _WorkItem
from tomorrow3 import threads as tomorrow_threads

# redis配置连接信息
redis_host = '127.0.0.1'
redis_password = ''
redis_port = 6379
redis_db = 0

redis_conn_instance = {}


class RedisQueue(object):
    """Simple Queue with Redis Backend"""

    def __init__(self, name, fliter_rep=False ,namespace='', **redis_kwargs):
        """
        
        :param name: 队列名称
        :param fliter_rep: 是否开始重复任务过滤
        :param namespace: 队列名前缀
        :param redis_kwargs: redis连接动态参数
        """
        """The default connection parameters are: host='localhost', port=6379, db=0"""
        if 'redis_conn' in redis_conn_instance:
            # print('****已经初始化过redis队列连接****')
            self.__db = redis_conn_instance.get('redis_conn')
        else:
            # print('****新初始化发布队列redis连接****')
            self.__db = redis.Redis(**redis_kwargs)
            redis_conn_instance['redis_conn'] = self.__db
        if namespace:
            self.key = '%s:%s' % (namespace, name)
        else:
            self.key = name
        self.fliter_rep = fliter_rep
        if fliter_rep:
            self.key_sets = self.key+':sets'

    def getdb(self):
        return self.__db

    def qsize(self):
        """Return the approximate size of the queue."""
        return self.__db.llen(self.key)

    def isempty(self):
        """Return True if the queue is empty, False otherwise."""
        return self.qsize() == 0

    def put(self, item):
        """Put item into the queue."""
        if self.fliter_rep:
            if self.__db.sismember(self.key_sets, item) is False:
                self.__db.lpush(self.key, item)
                self.__db.sadd(self.key_sets, item)
        else:
            self.__db.lpush(self.key, item)

    def clear(self):
        """delete the queue."""
        self.__db.delete(self.key)
        if self.key_sets:
            self.__db.delete(self.key_sets)

    def get(self, block=False, timeout=None):
        """Remove and return an item from the queue.
        If optional args block is true and timeout is None (the default), block
        if necessary until an item is available."""
        if block:
            item = self.__db.brpop(self.key, timeout=timeout)
        else:
            item = self.__db.rpop(self.key)
        if item:
            item = item.decode('utf-8')
        return item


class RedisCustomer(object):
    """reids队列消费类"""

    def __init__(self, queue_name, consuming_function: Callable = None, threads_num=50):
        """
        redis队列消费程序
        :param queue_name: 队列名称
        :param consuming_function: 队列消息取出来后执行的方法
        :param threads_num: 启动多少个队列线程
        """
        self.redis_quenen = RedisQueue(queue_name, host=redis_host, port=redis_port, db=redis_db,
                                       password=redis_password)
        self.consuming_function = consuming_function
        self.threads_num = threads_num
        self.threadpool = BoundedThreadPoolExecutor(threads_num)

    def start_consuming_message(self):
        print('*' * 50)
        while True:
            try:
                message = self.redis_quenen.get()
                if message:
                    self.threadpool.submit(self.consuming_function, message)
                else:
                    time.sleep(0.1)
            except:
                s = traceback.format_exc()
                print(s)
                time.sleep(0.1)

    @staticmethod
    def test_custom():
        def _print_(msg):
            print(msg)

        redis_customer = RedisCustomer('test', consuming_function=_print_)
        redis_customer._start_consuming_message()


class RedisPublish(object):
    """redis入队列类"""

    def __init__(self, queue_name, fliter_rep=False, max_push_size=1):
        """
        初始化消息发布队列
        :param queue_name: 队列名称(不包含命名空间)
        :param fliter_rep: 队列任务是否去重 True:去重  False:不去重
        :param threads_num: 并发线程数
        :param max_push_size: 
        """
        self.redis_quenen = RedisQueue(queue_name,fliter_rep=fliter_rep, host=redis_host, port=redis_port, db=redis_db,
                                       password=redis_password)
        # self.threads_num = threads_num
        self.max_push_size = max_push_size
        self.local_quenen = queue.Queue(maxsize=max_push_size + 1)
        self.pipe = self.redis_quenen.getdb().pipeline()

    @tomorrow_threads(50)
    def publish_redispy(self, **kwargs):
        """
        将多参数写入消息队列
        :param kwargs: 待写入参数 (a=3,b=4)
        :return: None
        """
        self.redis_quenen.put(json.dumps(kwargs))

    @tomorrow_threads(50)
    def publish_redispy_str(self, msg:str):
        """
        将字符串写入消息队列
        :param msg: 待写入消息字符串
        :return: None
        """
        self.redis_quenen.put(msg)

    def publish_redispy_list(self, msgs: list, publish_size=50):
        """
        批量写入redis队列
        :param msgs: 待写入字符串列表
        :param publish_size: 每次批量提交数量
        :return: 
        """
        pipe = self.redis_quenen.getdb().pipeline()
        for id in msgs:
            pipe.lpush(self.redis_quenen.key, id)
            if len(pipe) == publish_size:
                pipe.execute()
                print(f'*' * 10 + str(publish_size) + '*' * 10 + 'commit')
        if len(pipe)>0:
            pipe.execute()

    def publish_redispy_mutil(self, msg: str):
        """
        单笔写入,批量提交
        :param msg: 待写入字符串
        :return: None
        """
        self.local_quenen.put(msg)
        print(f'self.local_quenen.size:{self.local_quenen.qsize()}')
        if self.local_quenen.qsize() >= self.max_push_size:
            try:
                while self.local_quenen.qsize() > 0:
                    self.pipe.lpush(self.redis_quenen.key, self.local_quenen.get_nowait())
            except:
                traceback.print_exc()
            self.pipe.execute()
            print('commit'.center(16, '*'))

    def clear_quenen(self):
        """
        清空当前队列
        :return: 
        """
        self.redis_quenen.clear()


class BoundedThreadPoolExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers=None, thread_name_prefix=''):
        super().__init__(max_workers, thread_name_prefix)
        self._work_queue = queue.Queue(max_workers * 1)

    def submit(self, fn, *args, **kwargs):
        with self._shutdown_lock:
            if self._shutdown:
                raise RuntimeError('cannot schedule new futures after shutdown')
            f = Future()
            fn_deco = _deco(fn)
            w = _WorkItem(f, fn_deco, args, kwargs)
            self._work_queue.put(w)
            self._adjust_thread_count()
            return f


def _deco(f):
    @wraps(f)
    def __deco(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            print(e)

    return __deco


if __name__ == '__main__':

    quenen_name = 'test1'
    redis_pub = RedisPublish(queue_name=quenen_name,fliter_rep=True, max_push_size=50)

    result = [str(i) for i in range(1, 101)]

    for zz in result:
        redis_pub.publish_redispy(a=zz, b=zz, c=zz)  # 单条记录写入

    redis_pub.publish_redispy_list(result)  # 单线程提交列表任务

    for zz in result:
        redis_pub.publish_redispy_mutil(zz)  # 单线程批量写入2


    def print_msg(msg):
        print(json.loads(msg))


    # 多线程消费
    redis_customer = RedisCustomer(quenen_name, consuming_function=print_msg, threads_num=100)
    print(redis_customer.threads_num)
    redis_customer.start_consuming_message()
