"""
Utility functions for SciUnit.
"""

from __future__ import print_function
import os
import sys
import warnings
import inspect
import pkgutil
import importlib
import json
import re
import contextlib
import traceback
import inspect
import functools
from io import TextIOWrapper, StringIO
from datetime import datetime
from tempfile import TemporaryDirectory

import bs4
import nbformat
import nbconvert
from nbconvert.preprocessors import ExecutePreprocessor
from nbconvert.preprocessors.execute import CellExecutionError
from quantities.dimensionality import Dimensionality
from quantities.quantity import Quantity
from IPython.display import HTML, display

import sciunit
from sciunit.errors import Error
from .base import SciUnit, tkinter
from .base import PLATFORM, PYTHON_MAJOR_VERSION
from typing import Any, Callable, Dict, List, Optional, Tuple, Union, TextIO
from types import ModuleType
import unittest.mock
from pathlib import Path

mock = False  # mock is probably obviated by the unittest -b flag.

settings = {'PRINT_DEBUG_STATE': False,  # printd does nothing by default.
            'LOGGING': True,
            'PREVALIDATE': False,
            'KERNEL': ('ipykernel' in sys.modules),
            'CWD': os.path.realpath(sciunit.__path__[0])}


def warn_with_traceback(message: str, category, filename: str, lineno: int,
                        file: TextIO=None, line: str=None) -> None:
    """A function to use with `warnings.showwarning` to show a traceback.

    Args:
        message (str): A message that will be included in the warning.
        category ([type]): A category of the warning.
        filename (str): Name of the file that raises the warning
        lineno (int): Number of line in the file that causes this warning.
        file (TextIO, optional): A file object for recording the log. Defaults to None.
        line (str, optional): A line of source code to be included in the warning message. Defaults to None.
    """
    log = file if hasattr(file, 'write') else sys.stderr
    traceback.print_stack(file=log)
    log.write(warnings.formatwarning(
                message, category, filename, lineno, line))


def set_warnings_traceback(tb: bool=True) -> None:
    """Set to `True` to give tracebacks for all warnings, or `False` to restore
    default behavior.

    Args:
        tb (bool, optional): Defaults to True.
    """
    if tb:
        warnings._showwarning = warnings.showwarning
        warnings.showwarning = warn_with_traceback
        warnings.simplefilter("always")
    else:
        warnings.showwarning = warnings._showwarning
        warnings.simplefilter("default")


def dict_combine(*dict_list) -> dict:
    """Return the union of several dictionaries.
    Uses the values from later dictionaries in the argument list when
    duplicate keys are encountered.
    In Python 3 this can simply be {**d1, **d2, ...}
    but Python 2 does not support this dict unpacking syntax.

    Returns:
        dict: the dict from combining the dicts
    """
    return {k: v for d in dict_list for k, v in d.items()}


def rec_apply(func: Callable, n: int) -> Callable:
    """Used to determine parent directory n levels up
    by repeatedly applying os.path.dirname.

    Args:
        func (Callable): [description]
        n (int): [description]

    Returns:
        Callable: [description]
    """
    if n > 1:
        rec_func = rec_apply(func, n - 1)
        return lambda x: func(rec_func(x))
    return func


def printd_set(state: bool) -> None:
    """Enable the printd function.
    Call with True for all subsequent printd commands to be passed to print.
    Call with False to ignore all subsequent printd commands.

    Args:
        state (bool): [description]
    """

    global settings
    settings['PRINT_DEBUG_STATE'] = (state is True)


def printd(*args, **kwargs) -> bool:
    """Print if PRINT_DEBUG_STATE is True.

    Returns:
        bool: [description]
    """

    global settings
    if settings['PRINT_DEBUG_STATE']:
        print(*args, **kwargs)
        return True
    return False


if PYTHON_MAJOR_VERSION == 3:
    redirect_stdout = contextlib.redirect_stdout
else:  # Python 2
    raise Exception('Only Python 3 is supported')


def assert_dimensionless(value: Union[float, Quantity]) -> float:
    """Tests for dimensionlessness of input.
    If input is dimensionless but expressed as a Quantity, it returns the
    bare value. If it not, it raised an error.
    

    Args:
        value (Union[float, Quantity]): [description]

    Raises:
        TypeError: Score value must be dimensionless.

    Returns:
        float: [description]
    """

    if isinstance(value, Quantity):
        value = value.simplified
        if value.dimensionality == Dimensionality({}):
            value = value.base.item()
        else:
            raise TypeError("Score value %s must be dimensionless" % value)
    return value


class NotebookTools(object):
    """A class for manipulating and executing Jupyter notebooks.

    Attributes:
        path (str): Relative path to the parent directory of the notebook.
        gen_dir_name (str): Name of directory where files generated by do_notebook are stored.
        gen_file_level (int): Number of levels up from notebook directory where generated files are stored.
    """

    # Relative path to the parent directory of the notebook.
    path = ''

    # Name of directory where files generated by do_notebook are stored
    gen_dir_name = 'GeneratedFiles'

    # Number of levels up from notebook directory
    # where generated files are stored
    gen_file_level = 2

    def __init__(self, *args, **kwargs):
        super(NotebookTools, self).__init__(*args, **kwargs)
        self.fix_display()

    @classmethod
    def convert_path(cls, file: Union[str, list]) -> Union[str, int]:
        """Check to see if an extended path is given and convert appropriately.

        Args:
            file (Union[str, list]): [description]

        Returns:
            Union[str, int]: [description]
        """

        if isinstance(file, str):
            return file
        elif isinstance(file, list) and \
                all([isinstance(x, str) for x in file]):
            return "/".join(file)
        else:
            print("Incorrect path specified")
            return -1

    def get_path(self, file: str) -> str:
        """Get the full path of the notebook found in the directory
        specified by self.path.
        

        Args:
            file (str): [description]

        Returns:
            str: [description]
        """

        class_path = inspect.getfile(self.__class__)
        parent_path = os.path.dirname(class_path)
        path = os.path.join(parent_path, self.path, file)
        return os.path.realpath(path)

    def fix_display(self) -> None:
        """If this is being run on a headless system the Matplotlib
        backend must be changed to one that doesn't need a display.
        """

        try:
            tkinter.Tk()
        except (tkinter.TclError, NameError):  # If there is no display.
            try:
                import matplotlib as mpl
            except ImportError:
                pass
            else:
                printd("Setting matplotlib backend to Agg")
                mpl.use('Agg')

    def load_notebook(self, name: str) -> Tuple[nbformat.NotebookNode, Union[str, Path]]:
        """Loads a notebook file into memory.

        Args:
            name (str): name of the notebook file.

        Returns:
            Tuple[nbformat.NotebookNode, Union[str, Path]]: The notebook that was read and the path to the notebook file.
        """
        
        #with open(self.get_path('%s.ipynb' % name)) as f:
        #    nb = nbformat.read(f, as_version=4)
        file_path = self.get_path('%s.ipynb' % name)

        with open(file_path) as f:
            nb = nbformat.read(f, as_version=4)
        return nb, file_path

    def run_notebook(self, nb: nbformat.NotebookNode, file_path: Union[str, Path]) -> None:
        """Runs a loaded notebook file.

        Args:
            nb (nbformat.NotebookNode): The notebook that was loaded.
            f (Union[str, Path]): The path to the notebook file.

        Raises:
            Exception: The exception that is thrown when running the notebook.
        """

        if PYTHON_MAJOR_VERSION == 3:
            kernel_name = 'python3'
        else:
            raise Exception('Only Python 3 is supported')
        ep = ExecutePreprocessor(timeout=600, kernel_name=kernel_name)
        try:
            ep.preprocess(nb, {'metadata': {'path': '.'}})
        except CellExecutionError:
            msg = 'Error executing the notebook "%s".\n\n' % file_path
            msg += 'See notebook "%s" for the traceback.' % file_path
            print(msg)
            raise
        finally:
            nbformat.write(nb, file_path)

    def execute_notebook(self, name: str) -> None:
        """Loads and then runs a notebook file.

        Args:
            name (str): name of the notebook file.
        """

        warnings.filterwarnings("ignore", category=DeprecationWarning)
        nb, file_path = self.load_notebook(name)
        self.run_notebook(nb, file_path)

    def convert_notebook(self, name: str) -> None:
        """Converts a notebook into a python file.

        Args:
            name (str): name of the notebook file.
        """
        exporter = nbconvert.exporters.python.PythonExporter()
        relative_path = self.convert_path(name)
        file_path = self.get_path("%s.ipynb" % relative_path)
        code = exporter.from_filename(file_path)[0]
        self.write_code(name, code)
        self.clean_code(name, [])

    def convert_and_execute_notebook(self, name: str) -> None:
        """Converts a notebook into a python file and then runs it.

        Args:
            name (str): name of the notebook file.
        """
        self.convert_notebook(name)
        code = self.read_code(name)
        exec(code, globals())

    def gen_file_path(self, name: str) -> str:
        """Returns full path to generated files.  
        
        Checks to see if directory exists where generated files 
        are stored and creates one otherwise.
        
        Args:
            name (str): [description]

        Returns:
            str: [description]
        """
        relative_path = self.convert_path(name)
        file_path = self.get_path("%s.ipynb" % relative_path)
        parent_path = rec_apply(os.path.dirname,
                                self.gen_file_level)(file_path)
        # Name of generated file
        gen_file_name = name if isinstance(name, str) else name[1]
        gen_dir_path = self.get_path(os.path.join(parent_path,
                                                  self.gen_dir_name))
        # Create folder for generated files if needed
        if not os.path.exists(gen_dir_path):
            os.makedirs(gen_dir_path)
        new_file_path = self.get_path('%s.py' % os.path.join(gen_dir_path,
                                                             gen_file_name))
        return new_file_path

    def read_code(self, name: str) -> str:
        """Reads code from a python file called 'name'.

        Args:
            name (str): name of the python file.

        Returns:
            str: the code in the python file.
        """

        file_path = self.gen_file_path(name)
        with open(file_path) as f:
            code = f.read()
        return code

    def write_code(self, name: str, code: str) -> None:
        """Writes code to a python file called 'name', erasing the previous contents.

        Files are created in a directory specified by gen_dir_name
        (see function gen_file_path).
        File name is second argument of path.

        Args:
            name (str): name of the file.
            code (str): code to be added into the file.
        """

        
        file_path = self.gen_file_path(name)
        with open(file_path, 'w') as f:
            f.write(code)

    def clean_code(self, name: str, forbidden: List[Any]) -> str:
        """Remove lines containing items in 'forbidden' from the code.
        Helpful for executing converted notebooks that still retain IPython
        magic commands.

        Args:
            name (str): name of the notebook file
            forbidden (List[Any]): [description]

        Returns:
            str: [description]
        """

        code = self.read_code(name)
        code = code.split('\n')
        new_code = []
        for line in code:
            if [bad for bad in forbidden if bad in line]:
                pass
            else:
                # Magics where we want to keep the command
                allowed = ['time', 'timeit']
                line = self.strip_line_magic(line, allowed)
                if isinstance(line, list):
                    line = ' '.join(line)
                new_code.append(line)
        new_code = '\n'.join(new_code)
        self.write_code(name, new_code)
        return new_code

    @classmethod
    def strip_line_magic(cls, line: str, magics_allowed: List[str]) -> str:
        """Handles lines that contain get_ipython.run_line_magic() commands.

        Args:
            line (str): the line that contain get_ipython.run_line_magic() commands.
            magics_allowed (List[str]): [description]

        Returns:
            str: line after being stripped.
        """
        if PYTHON_MAJOR_VERSION == 3:
            stripped, magic_kind = cls.strip_line_magic_v3(line)
        else:
            raise Exception('Only Python 3 is supported')
        if line == stripped:
            printd("No line magic pattern match in '%s'" % line)
        if magic_kind and magic_kind not in magics_allowed:
            # If the part after the magic won't work, just get rid of it
            stripped = ""
        return stripped

    @classmethod
    def strip_line_magic_v3(cls, line: str) -> Tuple[str, str]:
        """strip_line_magic() implementation for Python 3.

        Args:
            line (str): [description]

        Returns:
            Tuple[str, str]: [description]
        """

        matches = re.findall("run_line_magic\(([^]]+)", line)
        if matches and matches[0]:  # This line contains the pattern
            match = matches[0]
            if match[-1] == ')':
                match = match[:-1]  # Just because the re way is hard
            magic_kind, stripped = eval(match)
        else:
            stripped = line
            magic_kind = ""
        return stripped, magic_kind

    def do_notebook(self, name: str) -> None:
        """Run a notebook file after optionally.
        converting it to a python file.

        Args:
            name (str): name of the notebook file.
        """
        CONVERT_NOTEBOOKS = int(os.getenv('CONVERT_NOTEBOOKS', True))
        s = StringIO()
        if mock:
            out = unittest.mock.patch('sys.stdout', new=MockDevice(s))
            err = unittest.mock.patch('sys.stderr', new=MockDevice(s))
            self._do_notebook(name, CONVERT_NOTEBOOKS)
            out.close()
            err.close()
        else:
            self._do_notebook(name, CONVERT_NOTEBOOKS)

    def _do_notebook(self, name: str, convert_notebooks: bool=False) -> None:
        """ Called by do_notebook to actually run the notebook.

        Args:
            name (str): name of the notebook file.
            convert_notebooks (bool): True if the notebook need conversion before executing. Defaults to False.
        """
        if convert_notebooks:
            self.convert_and_execute_notebook(name)
        else:
            self.execute_notebook(name)


class MockDevice(TextIOWrapper):
    """A mock device to temporarily suppress output to stdout
    Similar to UNIX /dev/null.
    """

    def write(self, s) -> None:
        """[summary]

        Args:
            s ([type]): [description]
        """
        if s.startswith('[') and s.endswith(']'):
            super(MockDevice, self).write(s)


def import_all_modules(package, skip: list=None, verbose: bool=False, 
                        prefix: str="", depth: int=0) -> None:
    """Recursively imports all subpackages, modules, and submodules of a
    given package.
    'package' should be an imported package, not a string.
    'skip' is a list of modules or subpackages not to import.
    Args:
        package ([type]): [description]
        skip (list, optional): [description]. Defaults to None.
        verbose (bool, optional): [description]. Defaults to False.
        prefix (str, optional): [description]. Defaults to "".
        depth (int, optional): [description]. Defaults to 0.
    """


    skip = [] if skip is None else skip

    for ff, modname, ispkg in pkgutil.walk_packages(path=package.__path__,
                                                    prefix=prefix,
                                                    onerror=lambda x: None):
        if ff.path not in package.__path__[0]:  # Solves weird bug
            continue
        if verbose:
            print('\t'*depth, modname)
        if modname in skip:
            if verbose:
                print('\t'*depth, '*Skipping*')
            continue
        module = '%s.%s' % (package.__name__, modname)
        subpackage = importlib.import_module(module)
        if ispkg:
            import_all_modules(subpackage, skip=skip,
                               verbose=verbose, depth=depth+1)


def import_module_from_path(module_path: str, name=None) -> ModuleType:
    """Import the python modual by the path to the file (module).

    Args:
        module_path (str): [description]
        name (str): [description]. Defaults to None.

    Returns:
        ModuleType: [description]
    """
    directory, file_name = os.path.split(module_path)
    if name is None:
        name = file_name.rstrip('.py')
        if name == '__init__':
            name = os.path.split(directory)[1]
    try:
        from importlib.machinery import SourceFileLoader
        sfl = SourceFileLoader(name, module_path)
        module = sfl.load_module()
    except ImportError:
        sys.path.append(directory)
        from importlib import import_module
        module_name = file_name.rstrip('.py')
        module = import_module(module_name)
        sys.path.pop()  # Remove the directory that was just added.
    return module


def dict_hash(d):
    return SciUnit.dict_hash(d)


def method_cache(by: str='value', method: str='run') -> Callable:
    """A decorator used on any model method which calls the model's 'method'
    method if that latter method has not been called using the current
    arguments or simply sets model attributes to match the run results if
    it has.

    Args:
        by (str, optional): [description]. Defaults to 'value'.
        method (str, optional): the method that being called. Defaults to 'run'.

    Returns:
        Callable: [description]
    """

    def decorate_(func):
        def decorate(*args, **kwargs):
            model = args[0]  # Assumed to be self.
            assert hasattr(model, method), \
                "Model must have a '%s' method." % method
            if func.__name__ == method:  # Run itself.
                method_args = kwargs
            else:  # Any other method.
                method_args = kwargs[method] if method in kwargs else {}
            # If there is no run cache.
            if not hasattr(model.__class__, 'cached_runs'):
                # Create the method cache.
                model.__class__.cached_runs = {}
            cache = model.__class__.cached_runs
            if by == 'value':
                model_dict = {key: value for key, value in
                              list(model.__dict__.items()) if key[0] != '_'}
                method_signature = SciUnit.dict_hash(
                    {'attrs': model_dict, 'args': method_args})  # Hash key.
            elif by == 'instance':
                method_signature = SciUnit.dict_hash(
                    {'id': id(model), 'args': method_args})  # Hash key.
            else:
                raise ValueError("Cache type must be 'value' or 'instance'")
            if method_signature not in cache:
                print(("Method with this signature not found in the cache. "
                       "Running..."))
                f = getattr(model, method)
                f(**method_args)
                cache[method_signature] = (datetime.now(),
                                           model.__dict__.copy())
            else:
                print(("Method with this signature found in the cache. "
                       "Restoring..."))
                _, attrs = cache[method_signature]
                model.__dict__.update(attrs)
            return func(*args, **kwargs)
        return decorate
    return decorate_



def log(*args, **kwargs) -> None:
    """[summary]
    """
    if settings['LOGGING']:
        if settings['KERNEL']:
            kernel_log(*args, **kwargs)
        else:
            non_kernel_log(*args, **kwargs)


def non_kernel_log(*args, **kwargs) -> None:
    """[summary]
    """
    args = [bs4.BeautifulSoup(x, "lxml").text
            if not isinstance(x, Exception) else x
            for x in args]
    print(*args, **kwargs)


def kernel_log(*args, **kwargs) -> None:
    """[summary]
    """
    with StringIO() as f:
        kwargs['file'] = f
        args = [u'%s' % arg for arg in args]
        print(*args, **kwargs)
        output = f.getvalue()
    display(HTML(output))


def config_get_from_path(config_path: str, key: str) -> int:
    """[summary]

    Args:
        config_path (str): [description]
        key (str): [description]

    Raises:
        Error: [description]
        Error: [description]
        Error: [description]

    Returns:
        int: [description]
    """
    try:
        with open(config_path) as f:
            config = json.load(f)
            value = config[key]
    except FileNotFoundError:
        raise Error("Config file not found at '%s'" % config_path)
    except json.JSONDecodeError:
        log("Config file JSON at '%s' was invalid" % config_path)
        raise Error("Config file not found at '%s'" % config_path)
    except KeyError:
        raise Error("Config file does not contain key '%s'" % key)
    return value


def config_get(key: str, default: int=None) -> int:
    """[summary]

    Args:
        key (str): [description]
        default (int, optional): [description]. Defaults to None.

    Raises:
        e: An exception raised during get config process.

    Returns:
        int: [description]
    """
    try:
        assert isinstance(key, str), "Config key must be a string"
        config_path = os.path.join(settings['CWD'], 'config.json')
        value = config_get_from_path(config_path, key)
    except Exception as e:
        if default is not None:
            log(e)
            log("Using default value of %s" % default)
            value = default
        else:
            raise e
    return value


def path_escape(path: str) -> str:
    """Escape a path by placing backslashes in front of disallowed characters.

    Args:
        path (str): original path.

    Returns:
        str: the new path with the backslashes.
    """
    for char in [' ', '(', ')']:
        path = path.replace(char, '\%s' % char)
    return path

############# The following code is from project cypy by Dr. Cyrus Omar ##################

def _generic_fn(*args, **kwargs): pass

_NotDefined = object()
_unhashable_object = object()

def get_fn(callable):
    """Returns the underlying function that will be called by the () operator.

        * For regular functions, returns ``callable``
        * For bound methods, returns ``callable.im_func``
        * For unbound methods, returns ``callable.__func__``
        * For classes, returns ``callable.__init__.__func__``.
        * For callable objects, returns ``callable.__call__.im_func``.

    """
    if inspect.isfunction(callable):
        return callable
    if inspect.ismethod(callable):
        try:
            return callable.__func__
        except AttributeError:
            return callable.__func__
    if inspect.isclass(callable):
        return callable.__init__.__func__
    if hasattr(callable, '__call__'):
        return callable.__call__.__func__
    return callable

def get_fn_or_method(callable):
    """Returns the underlying function or method that will be called by the () operator.

        * For regular functions and methods, returns ``callable``
        * For classes, returns ``callable.__init__``
        * For callable objects, returns ``callable.__call__``

    """
    if inspect.isfunction(callable) or inspect.ismethod(callable):
        return callable
    if inspect.isclass(callable):
        return callable.__init__
    return callable.__call__


def fn_available_argcount(callable):
    """Returns the number of explicit non-keyword arguments that the callable
    can be called with.

    Bound methods are called with an implicit first argument, so this takes
    that into account.

    Excludes *args and **kwargs declarations.
    """
    fn = get_fn_or_method(callable)
    if inspect.isfunction(fn):
        return fn.__code__.co_argcount
    else: # method
        if fn.__self__ is None:
            return fn.__func__.__code__.co_argcount
        else:
            return fn.__func__.__code__.co_argcount - 1


# see http://docs.python.org/reference/datamodel.html
_fn_args_flag = 0x04
fn_has_args = lambda callable: bool(get_fn(callable).__code__.co_flags
                                    & _fn_args_flag)
"""Returns whether the provided callable's underlying function takes *args."""

_fn_kwargs_flag = 0x08
fn_has_kwargs = lambda callable: bool(get_fn(callable).__code__.co_flags
                                      & _fn_kwargs_flag)
"""Returns whether the provided callable's underlying function takes **kwargs."""

_fn_generator_flag = 0x20
fn_is_generator = lambda callable: bool(get_fn(callable).__code__.co_flags
                                        & _fn_generator_flag)
"""Returns whether the provided callable's underlying function is a generator."""

_fn_future_division_flag = 0x2000
fn_uses_future_division = lambda callable: bool(get_fn(callable).__code__.co_flags
                                                & _fn_future_division_flag)
"""Returns whether the provided callable's underlying function uses future division."""

def fn_kwargs(callable):
    """Returns a dict with the kwargs from the provided function.

    Example

        >>> def x(a, b=0, *args, **kwargs): pass
        >>> func_kwargs(x) == { 'b': 0 }

    """
    fn = get_fn(callable)
    (args, _, _, defaults) = inspect.getargspec(fn)
    if defaults is None: return { }
    return dict(list(zip(reversed(args), reversed(defaults))))

def fn_arg_hash_function(fn):
    """Creates a hash function which will return the same hashable value if 
    passed a set of *args and **kwargs which are equivalent from the 
    perspective of a function call.
    
    That is, the order of keyword arguments, or the fact that an argument 
    without a default was called as a kwarg, will not produce a different hash.
    
    If any arguments provided are not hashable, a TypeError is raised.
    
    *args and **kwargs are supported.
    """
    fn = get_fn(fn)
    n_explicit = fn_available_argcount(fn)
    has_args = fn_has_args(fn)
    has_kwargs = fn_has_kwargs(fn)
    default_kwargs = fn_kwargs(fn)
    for name, value in list(default_kwargs.items()):  
        # store only hashes of values to prevent memory leaks
        try: default_kwargs[name] = hash(value)
        except TypeError: default_kwargs[name] = _unhashable_object
    explicit_kwarg_args = set(default_kwargs.keys())
    n_explicit_kwargs = len(explicit_kwarg_args)
    n_explicit_args = n_explicit - n_explicit_kwargs
    def _hashes(*args, **kwargs):        
        # explicit args
        i = 0
        n_explicit_args_ = min(len(args), n_explicit_args)
        while i < n_explicit_args_:
            #print args[i], 'is an explicit arg.'            
            yield hash(args[i])
    
            i += 1
    
        # explicit kwargs
        for name in explicit_kwarg_args:
            if len(args) > i:
                #print args[i], 'is a kwarg without a default'
                yield hash(args[i])
                i += 1                
            else:
                try:
                    #print kwargs[name], 'is a kwarg taken from kwargs'
                    yield hash(kwargs[name])
                except KeyError:
                    #print default_kwargs[name], 'is a kwarg taken from defaults'
                    yield default_kwargs[name]
    
        # *args
        if has_args:
            #print args[i:], 'is *args'
            yield hash(args[i:])
    
        # **kwargs
        # NOTE: we're treating the kwargs dicts as hashable even though 
        # technically they aren't... be wary if you define **kwargs and then
        # depend on its mutable characteristics.
        if has_kwargs:
            items = frozenset(item for item in list(kwargs.items())
                              if item[0] not in explicit_kwarg_args)
            #print items, 'is **kwargs items'
            yield hash(items)
        
    def hash_(*args, **kwargs):
        return tuple(_hashes(*args, **kwargs))
    
    return hash_

generic_arg_hash_function = fn_arg_hash_function(_generic_fn)

class intern(object):  
    # a class just so the name mangling mechanisms are invoked, deleted below

    @staticmethod
    def intern(cls_):
        """Transforms the provided class into an interned class.
        
        That is, initializing the class multiple times with the same arguments 
        (even if in a different order if using keyword arguments) should 
        always produce the same object, and __init__ should only be called the 
        first time for each unique set of arguments.
        
        This means that mutations will effectively be shared by all "instances" 
        of the class which shared initialization arguments. This might be 
        useful for storing metadata, for example.
        
            >>> class N(object):
            ...     def __init__(self, n):
            ...         self.n = n
            >>> N = intern(N)
            >>> five = N(5)
            >>> five2 = N(5)
            >>> five is five2
            True
            >>> five.is_odd = True
            >>> five2.is_odd
            True
            
        To enforce immutability of particular attributes, see the setonce 
        property modifier.
            
        The use of the term "intern" comes from the practice of string 
        interning used widely in programming languages, including Python. Look 
        it up.
        
        Can be used as a class decorator in Python 2.6+. Otherwise, use like 
        this:
        
            >>> class Test(object): pass
            >>> Test = intern(Test)
        
        .. Note:: Subclassing of intern classes with different __init__
                  arguments is tricky and probably should not be done if you 
                  don't understand precisely how this works.
                  
                  If you subclass with the same __init__ arguments (preferably
                  the same __init__) it will use the SAME pool. This can be used
                  to automate adding metadata as above, though you should 
                  probably just do that with a function.

        .. Note:: You can override the hash function used by providing a value
                  for __init__._intern__hash_function. This should take None
                  as the first argument (substituting for self) and then *args
                  and **kwargs (or your particular signature for __init__) and
                  produce a hash or hashable value.
                  
                  The default implementation is provided by fn_arg_hash_function
                  applied to __init__, or generic_arg_hash_function if that 
                  doesn't work.
        
        """
        cls_.__pool = { }
        
        __init__ = cls_.__init__
        try:
            __init__.__func__.__hash_function
        except AttributeError:
            try:
                __init__.__func__.__hash_function = \
                        fn_arg_hash_function(__init__)
            except (AttributeError, TypeError): pass
            
        # define an override for __new__ which looks in the cache first
        def __new__(cls, *args, **kwargs):
            """Override used by sciunit.utils.intern to cache instances of this class."""
            
            # check cache
            __init__ = cls.__init__
            try:
                hash_function = __init__.__func__.__hash_function
            except AttributeError:
                try:
                    hash_function = __init__.__func__.__hash_function = \
                                  fn_arg_hash_function(__init__)
                except (AttributeError, TypeError):
                    hash_function = generic_arg_hash_function
            
            try:
                # look-up object
                hash = hash_function(None, *args, **kwargs)  # none because self is not created yet
                obj = cls_.__pool[hash]
            except (TypeError, KeyError) as e:
                # if arguments not hashable or object not found, need to 
                # make a new object
                
                # restore the original new temporarily, if it existed
                orig_new = __new__.orig
                if orig_new is _NotDefined: del cls_.__new__
                else: cls_.__new__ = orig_new
                
                # create new object
                obj = cls(*args, **kwargs)
                
                # put it in ze pool
                if isinstance(e, KeyError):
                    cls_.__pool[hash] = obj
                
                # re-override __new__
                cls_.__new__ = __static_new__
                                
            # Return the instance but don't call __init__ since it was done 
            # when it was created the first time, see below for how this is 
            # done
            try: cls.__old_init = cls.__dict__['__init__']
            except KeyError: cls.__old_init = _NotDefined            
            cls.__init__ = _dummy_init
                
            return obj
            
        # save original __new__
        try: __new__.orig = staticmethod(cls_.__dict__['__new__'])
        except KeyError: 
            if cls_.__new__ is object.__new__:
                __new__.orig = _null_new
            else:
                __new__.orig = _NotDefined
        
        __static_new__ = staticmethod(__new__)
        cls_.__static_new__ = __static_new__
        cls_.__new__ = __static_new__
        return cls_

def _dummy_init(self, *args, **kwargs): #@UnusedVariable
    """Prevents __init__ from being called if returning a obj copy."""
    cls = type(self)
    old_init = cls._intern__old_init
    if old_init is _NotDefined:
        del cls.__init__
    else:
        cls.__init__ = old_init
    del cls._intern__old_init

@staticmethod
def _null_new(cls, *args, **kwargs): #@UnusedVariable
    # deprecation warning if you don't do this
    return object.__new__(cls)

def decorator(d):
    """Creates a proper decorator.

    If the default for the first (function) argument is None, creates a
    version which be invoked as either @decorator or @decorator(kwargs...).

    See examples below.
    """
    defaults = d.__defaults__
    if defaults and defaults[0] is None:
        # Can be applied as @decorator or @decorator(kwargs) because
        # first argument is None
        def decorate(fn=None, **kwargs):
            if fn is None:
                return functools.partial(decorate, **kwargs)
            else:
                decorated = d(fn, **kwargs)
                functools.update_wrapper(decorated, fn)
                return decorated
    else:
        # Can only be applied as @decorator
        def decorate(fn):
            decorated = d(fn)
            functools.update_wrapper(decorated, fn)
            return decorated
    functools.update_wrapper(decorate, d)
    return decorate

@decorator
def memoize(fn=None):
    """Caches the result of the provided function."""
    cache = { }
    arg_hash_fn = fn_arg_hash_function(fn)

    def decorated(*args, **kwargs):
        try:
            hash_ = arg_hash_fn(*args, **kwargs)
        except TypeError:
            return fn(*args, **kwargs)

        try:
            return cache[hash_]
        except KeyError:
            return_val = fn(*args, **kwargs)
            cache[hash_] = return_val
            return return_val
    functools.update_wrapper(decorated, fn)

    return decorated


class_intern = intern.intern

method_memoize = memoize