from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import _global_shutdown, _WorkItem, BrokenProcessPool
from sys import version_info

import cloudpickle


def _apply_cloudpickle(fn, /, *args, **kwargs):
    fn = cloudpickle.loads(fn)
    return fn(*args, **kwargs)


class CloudpickleProcessPoolExecutor(ProcessPoolExecutor):
    """
    This executor behaves like `concurrent.futures.ProcessPoolExecutor`, except that
    non-pickleable callables may also be submit (e.g. dynamically defined functions).

    This is accomplished by replacing the `pickle` backend of the
    `concurrent.futures.ProcessPoolExecutor` with a backend from `cloudpickle` when
    serializing the callable.

    This solution comes from u/mostsquares @ stackoverflow:
    https://stackoverflow.com/questions/62830970/submit-dynamically-loaded-functions-to-the-processpoolexecutor

    Note: Arguments and return values must still be regularly pickleable.
    """

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(
            _apply_cloudpickle, cloudpickle.dumps(fn), *args, **kwargs
        )


class CloudLoadsFuture(Future):
    def result(self, timeout=None):
        result = super().result(timeout=timeout)
        if isinstance(result, bytes):
            result = cloudpickle.loads(result)
        return result


class CloudPickledCallable:
    def __init__(self, fnc: callable):
        self.fnc_serial = cloudpickle.dumps(fnc)

    def __call__(self, /, dumped_args, dumped_kwargs):
        fnc = cloudpickle.loads(self.fnc_serial)
        args = cloudpickle.loads(dumped_args)
        kwargs = cloudpickle.loads(dumped_kwargs)
        return cloudpickle.dumps(fnc(*args, **kwargs))

    @classmethod
    def dumps(cls, stuff):
        return cloudpickle.dumps(stuff)


class CloudProcessPoolExecutor(ProcessPoolExecutor):
    def submit(self, fn, /, *args, **kwargs):
        return self._submit(
            CloudPickledCallable(fn),
            CloudPickledCallable.dumps(args),
            CloudPickledCallable.dumps(kwargs)
        )

    def _submit(self, fn, /, *args, **kwargs):
        """
        We override the regular `concurrent.futures.ProcessPoolExecutor` to use our
        custom future that unpacks cloudpickled results.

        This approach is simple, but the brute-force nature of it means we manually
        accommodate different implementations of `ProcessPoolExecutor` in different
        python versions.
        """
        if version_info.major != 3:
            raise RuntimeError(
                f"{self.__class__} is only built for python3, but got "
                f"{version_info.major}"
            )

        if version_info.minor == 8:
            return self._submit_3_8(fn, *args, **kwargs)
        elif version_info.minor >= 9:
            return self._submit_3_gt9(fn, *args, **kwargs)
        else:
            raise RuntimeError(
                f"{self.__class__} is only built for python 3.8+, but got "
                f"{version_info.major}.{version_info.minor}."
            )

    def _submit_3_gt9(self, fn, /, *args, **kwargs):
        with self._shutdown_lock:
            if self._broken:
                raise BrokenProcessPool(self._broken)
            if self._shutdown_thread:
                raise RuntimeError('cannot schedule new futures after shutdown')
            if _global_shutdown:
                raise RuntimeError('cannot schedule new futures after '
                                   'interpreter shutdown')

            f = CloudLoadsFuture()
            w = _WorkItem(f, fn, args, kwargs)

            self._pending_work_items[self._queue_count] = w
            self._work_ids.put(self._queue_count)
            self._queue_count += 1
            # Wake up queue management thread
            self._executor_manager_thread_wakeup.wakeup()

            if self._safe_to_dynamically_spawn_children:
                self._adjust_process_count()
            self._start_executor_manager_thread()
            return f

    def _submit_3_8(*args, **kwargs):
        if len(args) >= 2:
            self, fn, *args = args
        elif not args:
            raise TypeError("descriptor 'submit' of 'ProcessPoolExecutor' object "
                            "needs an argument")
        elif 'fn' in kwargs:
            fn = kwargs.pop('fn')
            self, *args = args
            import warnings
            warnings.warn("Passing 'fn' as keyword argument is deprecated",
                          DeprecationWarning, stacklevel=2)
        else:
            raise TypeError('submit expected at least 1 positional argument, '
                            'got %d' % (len(args) - 1))

        with self._shutdown_lock:
            if self._broken:
                raise BrokenProcessPool(self._broken)
            if self._shutdown_thread:
                raise RuntimeError('cannot schedule new futures after shutdown')
            if _global_shutdown:
                raise RuntimeError('cannot schedule new futures after '
                                   'interpreter shutdown')

            f = CloudLoadsFuture()
            w = _WorkItem(f, fn, args, kwargs)

            self._pending_work_items[self._queue_count] = w
            self._work_ids.put(self._queue_count)
            self._queue_count += 1
            # Wake up queue management thread
            self._queue_management_thread_wakeup.wakeup()

            self._start_queue_management_thread()
            return f
