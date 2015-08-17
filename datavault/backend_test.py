import datetime
import h5py
import numpy as np
import os
import pytest
import random
import string
import time
import tempfile

import labrad.types as T
import labrad.units as U

from twisted.trial import unittest
from twisted.internet import reactor, task

from servers.datavault import backend, errors


def _unique_filename(suffix='.hdf5'):
    return tempfile.mktemp(prefix='dvtest', suffix=suffix)


def _remove_file_if_exists(name):
    try:
        os.unlink(name)
    except OSError:
        pass


class _TestCase(unittest.TestCase):
    def assert_arrays_equal(self, first, second):
        self.failUnless(
                np.array_equal(first, second),
                msg=('Arrays not equal.\n'
                        'first: {}\n'
                        'second: {}'.format(first, second)))


class UtilityMethodsTest(_TestCase):
    def test_time_to_str(self):
        time = datetime.datetime(2012, 9, 21, 3, 14, 15)
        actual = backend.time_to_str(time)
        expected = '2012-09-21, 03:14:15'
        self.failUnlessEqual(expected, actual)

    def test_time_from_str(self):
        time_string = '2012-09-21, 03:14:15'
        actual = backend.time_from_str(time_string)
        expected = datetime.datetime(2012, 9, 21, 3, 14, 15)
        self.failUnlessEqual(expected, actual)

    def test_labrad_urlencode(self):
        url_string = 'foo.bar/baz'
        actual = backend.labrad_urlencode(url_string)
        expected = ('data:application/labrad;base64,'
                    'AAAAAXMAAAAPAAAAC2Zvby5iYXIvYmF6')
        self.failUnlessEqual(expected, actual)

    def test_labrad_urldecode(self):
        url_string = ('data:application/labrad;base64,'
                      'AAAAAXMAAAAPAAAAC2Zvby5iYXIvYmF6')
        actual = backend.labrad_urldecode(url_string)
        expected = 'foo.bar/baz'
        self.failUnlessEqual(expected, actual)

    def test_labrad_urldecode_incorrect_prefix(self):
        url_string = ('labrad;base64,AAAAAXMAAAAPAAAAC2Zvby5iYXIvYmF6')
        self.failUnlessRaises(
                ValueError, backend.labrad_urldecode, url_string)


class _MockFile(object):
    def __init__(self):
        self.is_open = True

    def close(self):
        self.is_open = False


class _MockFileOpener(object):
    def __init__(self):
        self.file = None
        self.args = None
        self.kwargs = None

    def __call__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.file = _MockFile()
        return self.file


class SelfClosingFileTest(_TestCase):
    """Tests for the SelfClosingFile."""

    def setUp(self):
        self.close_timeout_sec = 1
        self.opener = _MockFileOpener()
        self.clock = task.Clock()
        self.file = backend.SelfClosingFile(opener=self.opener,
                                            timeout=self.close_timeout_sec,
                                            open_args=('1', '2', '3'),
                                            open_kw={'a':'b', 'c':'d'},
                                            reactor=self.clock)

    def test_file_doesnt_open_if_no_touch(self):
        opener = _MockFileOpener()
        self.file = backend.SelfClosingFile(opener=opener,
                                            timeout=self.close_timeout_sec,
                                            open_args=('1', '2', '3'),
                                            open_kw={'a':'b', 'c':'d'},
                                            touch=False)
        self.failUnless(opener.file is None, msg='File was opened on init')

    def test_file_opens_on_init(self):
        self.failUnless(self.opener.file.is_open, msg='File not opened on init')
        self.failUnlessEqual(
                self.opener.args, ('1', '2', '3'), 'File args not set')
        self.failUnlessEqual(
                self.opener.kwargs, {'a':'b', 'c':'d'}, 'File kwargs not set')

    def test_closes_file_after_timeout(self):
        self.close_callback_called = False
        def onCloseCallback(self_closing_file):
            self.close_callback_called = True
        self.file.onClose(onCloseCallback)
        # Advance clock to close the self closing file.
        self.clock.advance(self.close_timeout_sec)
        self.failIf(self.opener.file.is_open,
                    msg='File not closed after timeout')
        self.failUnless(self.close_callback_called,
                    msg='Registered callback not called!')


# Dependent and Independent variables used for testing IniData and HDF5MetaData.
_INDEPENDENTS = [
        backend.Independent(
                label='FirstVariable',
                shape=(1,),
                datatype='v',
                unit='Ghz'),
        backend.Independent(
                label='SecondVariable',
                shape=(1,),
                datatype='v',
                unit='Kelvin')]

_DEPENDENTS = [
        backend.Dependent(
                label='Cents',
                legend='OnlyDependent',
                shape=(1,),
                datatype='v',
                unit='Dollars')]



class _MetadataTest(_TestCase):
    def run(self, result):
        """Prevents the base test class from running tests."""
        if issubclass(_MetadataTest, type(self)):
            return
        super(_MetadataTest, self).run(result)


    def get_data(self):
        """Returns metadata instance to test."""
        pass

    def test_initialize_independents(self):
        data = self.get_data()
        data.initialize_info('FooTitle', _INDEPENDENTS, [])
        self.failUnlessEqual(data.getRowType(), '*(v[Ghz],v[Kelvin])')
        self.failUnlessEqual(data.getTransposeType(), '(*v[Ghz],*v[Kelvin])')
        self.failUnlessEqual(data.getIndependents(), _INDEPENDENTS)
        self.failUnlessEqual(data.getDependents(), [])

    def test_initialize_dependents(self):
        data = self.get_data()
        data.initialize_info('FooTitle', [], _DEPENDENTS)
        self.failUnlessEqual(data.getRowType(), '*(v[Dollars])')
        self.failUnlessEqual(data.getTransposeType(), '(*v[Dollars])')
        self.failUnlessEqual(data.getDependents(), _DEPENDENTS)
        self.failUnlessEqual(data.getIndependents(), [])

    def test_initialize_info(self):
        data = self.get_data()
        data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)
        self.failUnlessEqual(data.getRowType(), '*(v[Ghz],v[Kelvin],v[Dollars])')
        self.failUnlessEqual(
                data.getTransposeType(), '(*v[Ghz],*v[Kelvin],*v[Dollars])')
        self.failUnlessEqual(data.getDependents(), _DEPENDENTS)
        self.failUnlessEqual(data.getIndependents(),_INDEPENDENTS)

    def test_add_param(self):
        data = self.get_data()
        data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)
        self.failUnlessEqual(data.getParamNames(), [])
        param = (True, np.int32(100), U.Complex(1.j+0xdeadbeef, U.inch))
        data.addParam('Param1', param)
        self.failUnlessEqual(data.getParamNames(), ['Param1'])
        self.failUnlessEqual(data.getParameter('Param1'), param)
        self.failUnlessRaises(
                errors.BadParameterError,
                data.getParameter,
                'param1')
        self.failUnlessEqual(
                data.getParameter('param1', case_sensitive=False), param)

    def test_add_param_already_added(self):
        data = self.get_data()
        data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)
        self.failUnlessEqual(data.getParamNames(), [])
        param = (True, np.int32(100), U.Complex(1.j+0xdeadbeef, U.inch))
        data.addParam('Param1', param)
        self.failUnlessRaises(
                errors.ParameterInUseError,
                data.addParam,
                'Param1', param)

    def test_add_comment(self):
        data = self.get_data()
        data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)
        self.failUnlessEqual(data.numComments(), 0)
        data.addComment('foo user', 'bar comment')
        self.failUnlessEqual(data.numComments(), 1)
        comments, _ = data.getComments(None, 0)
        self.failUnlessEqual(len(comments), 1)
        self.failUnlessEqual(comments[0][1], 'foo user')
        self.failUnlessEqual(comments[0][2], 'bar comment')

    def test_iterate_get_comments(self):
        data = self.get_data()
        data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)
        for i in xrange(3):
            data.addComment('user {}'.format(i), '{}'.format(i))
        self.failUnlessEqual(data.getComments(0, 0), ([], 0))
        comments_0, next_pos = data.getComments(1, 0)
        self.failUnlessEqual(next_pos, 1)
        self.failUnlessEqual(len(comments_0), 1)
        self.failUnlessEqual(comments_0[0][1], 'user 0')
        self.failUnlessEqual(comments_0[0][2], '0')
        comments_1, next_pos = data.getComments(2, 1)
        self.failUnlessEqual(next_pos, 3)
        self.failUnlessEqual(len(comments_1), 2)
        self.failUnlessEqual(comments_1[0][1], 'user 1')
        self.failUnlessEqual(comments_1[0][2], '1')
        self.failUnlessEqual(comments_1[1][1], 'user 2')
        self.failUnlessEqual(comments_1[1][2], '2')


class IniDataTest(_MetadataTest):
    _TEST_INI_FILE = '''
[General]
title=TestTitle
created=2012-09-21, 03:14:15
accessed=2012-09-22, 03:14:15
modified=2012-09-23, 03:14:15
independent=2
dependent=1
parameters=1
Comments=2

[Independent 1]
label=FirstVariable
units=GHz

[Independent 2]
label=SecondVariable
units=Kelvin

[Dependent 1]
label=OnlyDependent
units=Dollars
category=Cents

[Parameter 1]
label=A Parameter
data=[12,3,{'a':0}]

[Comments]
c0=('2012-09-24, 03:14:15','bar','baz')
c1=('2012-09-25, 03:14:15','fizz','buzz')
'''

    def setUp(self):
        self.infofilename = _unique_filename(suffix='.ini')

    def tearDown(self):
        _remove_file_if_exists(self.infofilename)

    def get_data(self):
        return backend.IniData()

    def load_test_data(self):
        # First save the test it to a file.
        with file(self.infofilename, 'w') as f:
            f.write(self._TEST_INI_FILE)
        # Now load it into a new IniData
        data = backend.IniData()
        data.infofile = self.infofilename
        data.load()
        return data

    def test_load_dtype(self):
        data = self.load_test_data()
        self.failUnlessEqual(
                data.dtype,
                np.dtype([('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')]))

    def test_load_independents(self):
        data = self.load_test_data()
        independents = data.getIndependents()
        self.failUnlessEqual(len(independents), 2)
        self.failUnlessEqual(independents[0].label, 'FirstVariable')
        self.failUnlessEqual(independents[0].unit, 'GHz')
        self.failUnlessEqual(independents[1].label, 'SecondVariable',)
        self.failUnlessEqual(independents[1].unit, 'Kelvin')

    def test_load_dependents(self):
        data = self.load_test_data()
        dependents = data.getDependents()
        self.failUnlessEqual(len(dependents), 1)
        self.failUnlessEqual(dependents[0].label, 'Cents')
        self.failUnlessEqual(dependents[0].unit, 'Dollars')
        self.failUnlessEqual(dependents[0].legend, 'OnlyDependent')

    def test_load_parameters(self):
        data = self.load_test_data()
        parameters = data.getParamNames()
        self.failUnlessEqual(len(parameters), 1)
        self.failUnlessEqual(parameters[0], 'A Parameter')
        self.failUnlessEqual(data.getParameter('A Parameter'), [12, 3, {'a': 0}])

    def test_load_comments(self):
        data = self.load_test_data()
        self.failUnlessEqual(data.numComments(), 2)
        comments, next_comment_position = data.getComments(None, 0)
        self.failUnlessEqual(next_comment_position, 2)
        self.failUnlessEqual(len(comments), 2)
        self.failUnlessEqual(
                comments[0],
                (datetime.datetime(2012, 9, 24, 3, 14, 15), 'bar', 'baz'))
        self.failUnlessEqual(
                comments[1],
                (datetime.datetime(2012, 9, 25, 3, 14, 15), 'fizz', 'buzz'))

    def test_load_rowtype(self):
        data = self.load_test_data()
        self.failUnlessEqual(data.getRowType(), '*(v[GHz],v[Kelvin],v[Dollars])')

    def test_load_transpose_type(self):
        data = self.load_test_data()
        self.failUnlessEqual(
                data.getTransposeType(), '(*v[GHz],*v[Kelvin],*v[Dollars])')

    def test_add_complicated_param(self):
        data = backend.IniData()
        data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)
        self.failUnlessEqual(data.getParamNames(), [])
        data.addParam('Param1', ('really', {'complex' : 0xdeadbeef}, ['data']))
        self.failUnlessEqual(data.getParamNames(), ['Param1'])
        self.failUnlessEqual(
                data.getParameter('Param1'),
                ('really', {'complex': 0xdeadbeef}, ['data']))
        self.failUnlessRaises(
                errors.BadParameterError,
                data.getParameter,
                'param1')
        self.failUnlessEqual(
                data.getParameter('param1', case_sensitive=False),
                ('really', {'complex': 0xdeadbeef}, ['data']))

    def test_save_reload(self):
        # Generate some data to save.
        data_to_save = backend.IniData()
        data_to_save.initialize_info(
                'FooTitle', _INDEPENDENTS, _DEPENDENTS)
        data_to_save.addComment('foo user', 'bar comment')
        data_to_save.addParam('Param1', [100])
        # Save it.
        data_to_save.infofile = self.infofilename
        data_to_save.save()
        # Create a new IniData and read the saved file.
        data = backend.IniData()
        data.infofile = self.infofilename
        data.load()

        # Check that it's all there.
        self.failUnlessEqual(data.getDependents(), _DEPENDENTS)
        self.failUnlessEqual(data.getIndependents(), _INDEPENDENTS)
        comments, _ = data.getComments(1, 0)
        self.failUnlessEqual(len(comments), 1)
        self.failUnlessEqual(comments[0][1], 'foo user')
        self.failUnlessEqual(comments[0][2], 'bar comment')
        self.failUnlessEqual(data.getParamNames(), ['Param1'])
        self.failUnlessEqual(data.getParameter('Param1'), [100])
        self.failUnlessEqual(data.getRowType(), '*(v[Ghz],v[Kelvin],v[Dollars])')
        self.failUnlessEqual(
                data.getTransposeType(), '(*v[Ghz],*v[Kelvin],*v[Dollars])')


class _MockAttrs(dict):
    """Mock Attributes class for use in the _MockDataset."""
    def create(self, name, data, dtype):
        self[name] = np.asarray(data, dtype=dtype)


class _MockDataset(object):
    """Mock Dataset class to use in the HDF5MetaDataTest."""
    def __init__(self):
        self.attrs = _MockAttrs()


class HDF5MetaDataTest(_MetadataTest):

    def get_data(self):
        data = backend.HDF5MetaData()
        data.dataset = _MockDataset()
        return data


class _BackendDataTestCase(_TestCase):
    def assert_data_in_backend(self, backend_data, expected_data):
        """Checks that the backend data contains the expected data.

        Note that expected_data should be at least 2 rows.
        """
         # Read using .data.
        if hasattr(backend_data, 'data'):
            read_data = backend_data.data
            self.assert_arrays_equal(read_data, expected_data)

        # Read using getData for all data.
        read_data, next_pos = backend_data.getData(None, 0, False, None)
        self.assert_arrays_equal(read_data, expected_data)
        self.failUnlessEqual(next_pos, len(expected_data))

        # Read using getData for first row of data.
        read_data, next_pos = backend_data.getData(1, 0, False, None)
        self.assert_arrays_equal(read_data[0], expected_data[0])
        self.failUnlessEqual(next_pos, 1)

        # Read using getData for first row of data.
        read_data, next_pos = backend_data.getData(1, 1, False, None)
        self.assert_arrays_equal(read_data[0], expected_data[1])
        self.failUnlessEqual(next_pos, 2)

class CsvListDataTest(_BackendDataTestCase):

    def setUp(self):
        self.filename = _unique_filename(suffix='.raw')
        self.clock = task.Clock()
        self.data = self.get_backend_data()
        # Initialize the metadata.
        self.data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)

    def tearDown(self):
        _remove_file_if_exists(self.filename)
        _remove_file_if_exists(self.filename[:-4] + '.ini')

    def get_backend_data(self):
        return backend.CsvListData(self.filename, reactor=self.clock)

    def test_empty_data_read(self):
        read_data = self.data.data
        self.failUnlessEqual(read_data, [])

    def test_add_data_then_read(self):
        self.data.addData([[1, 2, 3]])
        self.data.addData([[4, 5, 6]])
        self.assert_data_in_backend(self.data, [[1, 2, 3], [4, 5, 6]])

    def test_add_data_wrong_number_of_columns(self):
        self.failUnlessRaises(errors.BadDataError, self.data.addData, [(1, 2)])
        self.failUnlessRaises(
               errors.BadDataError, self.data.addData, [(1, 2, 3, 4)])

    def test_read_from_file(self):
         # Add some data and save it to a file.
        self.data.addData([[1, 2, 3], [4, 5, 6]])
        self.data.save()
        del self.data

        # Load it back.
        data = self.get_backend_data()
        data.load()
        self.assert_data_in_backend(data, [[1, 2, 3], [4, 5, 6]])
        self.failUnlessTrue(data.hasMore(0))
        self.failUnlessTrue(data.hasMore(1))
        self.failUnlessFalse(data.hasMore(2))

    def test_add_data_wrong_number_of_columns(self):
        self.failUnlessRaises(errors.BadDataError, self.data.addData, [(1, 2)])
        self.failUnlessRaises(
               errors.BadDataError, self.data.addData, [(1, 2, 3, 4)])


class _BackendDataTest(_BackendDataTestCase):
    """Base tests for data backends."""

    def run(self, result):
        """Prevents the base test class from running tests."""
        if issubclass(_BackendDataTest, type(self)):
            return
        super(_BackendDataTest, self).run(result)

    def get_backend_data(self, filename):
        return None

    def test_add_data_then_read(self):
        data0 = np.recarray(
            (1, ),
            dtype=[('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')])
        data0[0] = (1, 2, 3)
        data1 = np.recarray(
            (1, ),
            dtype=[('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')])
        data1[0] = (4, 5, 6)
        self.data.addData(data0)
        self.data.addData(data1)
        self.assert_data_in_backend(self.data, [[1, 2, 3], [4, 5, 6]])

    def test_add_recarray_data_then_read(self):
        data = np.recarray(
            (2, ),
            dtype=[('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')])
        data[0] = (1, 2, 3)
        data[1] = (4, 5, 6)
        self.data.addData(data)
        self.assert_data_in_backend(self.data, [[1, 2, 3], [4, 5, 6]])

    def test_read_from_file(self):
         # Add some data and save it to a file.
        data_to_save = np.recarray(
            (2, ),
            dtype=[('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')])
        data_to_save[0] = (1, 2, 3)
        data_to_save[1] = (4, 5, 6)
        self.data.addData(data_to_save)
        self.data.save()
        del self.data

        # Load it back.
        data = self.get_backend_data(self.filename)
        data.load()
        self.assert_data_in_backend(data, [[1, 2, 3], [4, 5, 6]])
        self.failUnlessTrue(data.hasMore(0))
        self.failUnlessTrue(data.hasMore(1))
        self.failUnlessFalse(data.hasMore(2))

    def test_get_data_transpose(self):
        data_to_add = np.recarray(
            (2, ),
            dtype=[('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')])
        data_to_add[0] = (1, 2, 3)
        data_to_add[1] = (4, 5, 6)
        self.data.addData(data_to_add)

        self.failUnlessRaises(
               RuntimeError,
               self.data.getData,
               None,
               0,
               True,
               None)


class CsvNumpyDataTest(_BackendDataTest):

    def setUp(self):
        self.filename = _unique_filename(suffix='raw')
        self.files_to_remove = []
        self.clock = task.Clock()
        self.data = self.get_backend_data(self.filename)
        # Initialize the metadata.
        self.data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)

    def tearDown(self):
        for name in self.files_to_remove:
            _remove_file_if_exists(name)
            _remove_file_if_exists(name[:-4] + '.ini')


    def get_backend_data(self, filename):
        self.files_to_remove.append(filename)
        return backend.CsvNumpyData(filename, reactor=self.clock)

    def test_empty_data_read(self):
        read_data = self.data.data
        self.failUnlessEqual(read_data.dtype, np.dtype(float))
        self.failUnlessEqual(read_data.size, 0)
        self.failUnlessEqual(read_data[0].size, 0)

    def test_add_data_wrong_number_of_columns(self):
        self.failUnlessRaises(errors.BadDataError, self.data.addData, [(1, 2)])
        self.failUnlessRaises(
               errors.BadDataError, self.data.addData, [(1, 2, 3, 4)])

class ExtendedHDF5DataTest(_BackendDataTest):

    def setUp(self):
        self.filename = _unique_filename(suffix='.hdf5')
        self.files_to_remove = []
        self.clock = task.Clock()
        self.data = self.get_backend_data(self.filename)
        # Initialize the metadata.
        self.data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)

    def tearDown(self):
        for name in self.files_to_remove:
            _remove_file_if_exists(name)

    def get_backend_data(self, filename):
        self.files_to_remove.append(filename)
        fh = backend.SelfClosingFile(
                h5py.File, open_args=(filename, 'a'), reactor=self.clock)
        return backend.ExtendedHDF5Data(fh)

    def test_empty_data_read(self):
        read_data, _ =  self.data.getData(None, 0, False, None)
        self.failUnlessEqual(read_data, [])

    def test_get_data_transpose(self):
        data_to_add = np.recarray(
            (2, ),
            dtype=[('f0', '<f8'), ('f1', '<f8'), ('f2', '<f8')])
        data_to_add[0] = (1, 2, 3)
        data_to_add[1] = (4, 5, 6)
        self.data.addData(data_to_add)

        actual, next_pos = self.data.getData(None, 0, True, None)
        self.failUnlessEqual(next_pos, 2)
        self.failUnlessEqual(len(actual), 3)
        self.assert_arrays_equal(actual, [[1, 4], [2, 5], [3, 6]])

    def test_initialize_info_bad_vars(self):
        bad_independents = [
                        backend.Independent(
                        label='FirstVariable',
                        shape=(1,),
                        datatype='f',
                        unit='Ghz')]
        data = self.get_backend_data(self.filename)
        self.failUnlessRaises(
                RuntimeError,
                data.initialize_info,
                'FooTitle',
                bad_independents,
                [])

        bad_dependents = [
                backend.Dependent(
                        label='Cents',
                        legend='OnlyDependent',
                        shape=(1,),
                        datatype='t',
                        unit='Dollars')]
        self.failUnlessRaises(
                RuntimeError,
                data.initialize_info,
                'FooTitle',
                bad_dependents,
                [])

    def test_initialize_type_i(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1,),
                datatype='i',
                unit='')
        data.initialize_info('Foo', [independent], [])
        self.failUnlessEqual(data.dtype, np.dtype([('f0', '<i4')]))

    def test_initialize_type_t(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1,),
                datatype='t',
                unit='')
        data.initialize_info('Foo', [independent], [])
        self.failUnlessEqual(data.dtype, np.dtype([('f0', '<i8')]))

    def test_initialize_type_c(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1,),
                datatype='c',
                unit='')
        data.initialize_info('Foo', [independent], [])
        self.failUnlessEqual(data.dtype, np.dtype([('f0', '<c16')]))

    def test_initialize_type_v(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1,),
                datatype='v',
                unit='')
        data.initialize_info('Foo', [independent], [])
        self.failUnlessEqual(data.dtype, np.dtype([('f0', '<f8')]))

    def test_initialize_type_v(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1,),
                datatype='s',
                unit='')
        data.initialize_info('Foo', [independent], [])
        self.failUnlessEqual(data.dtype, np.dtype([('f0', 'O')]))


    def test_initialize_type_unknown(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1,),
                datatype='x',
                unit='')
        self.failUnlessRaises(
                RuntimeError,
                data.initialize_info,
                'FooTitle',
                [independent],
                [])

    def test_add_complex_data_array_then_read(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(2, 2),
                datatype='c',
                unit='V')
        data.initialize_info('Foo', [independent], [])
        data_entry = np.recarray(
            (1, ),
            dtype=[('f0', '<c16', (2, 2))])
        data_entry[0][0][0] = [0j, 1j]
        data_entry[0][0][1] = [1j, 0j]
        data.addData(data_entry)
        added_data, _ = data.getData(None, 0, False, None)
        self.assert_arrays_equal(added_data[0][0], [[0j, 1j], [1j, 0j]])

    def test_add_complex_data_array_then_read_objs(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(1, ),
                datatype='s',
                unit='')
        data.initialize_info('Foo', [independent], [])
        data_entry = np.recarray(
            (1, ),
            dtype=[('f0', 'O')])
        data_entry[0] = ({'a' : 0}, )
        data.addData(data_entry)
        added_data, _ = data.getData(None, 0, False, None)
        self.failUnlessEqual(added_data[0][0], "{'a': 0}")

    def test_add_string_array_column(self):
        name = _unique_filename()
        data = self.get_backend_data(name)
        independent = backend.Independent(
                label='NewVariable',
                shape=(2, 0),
                datatype='s',
                unit='')
        self.failUnlessRaises(
                ValueError,
                data.initialize_info,
                'FooTitle',
                [independent],
                [])


class SimpleHDF5DataTest(_BackendDataTest):

    def setUp(self):
        self.filename = _unique_filename()
        self.filenames_to_remove = []
        self.clock = task.Clock()
        self.data = self.get_backend_data(self.filename)
        # Initialize the metadata.
        self.data.initialize_info('FooTitle', _INDEPENDENTS, _DEPENDENTS)

    def tearDown(self):
        for name in self.filenames_to_remove:
            _remove_file_if_exists(name)

    def get_backend_data(self, filename):
        self.filenames_to_remove.append(filename)
        fh = backend.SelfClosingFile(
                h5py.File, open_args=(filename, 'a'), reactor=self.clock)
        return backend.SimpleHDF5Data(fh)

    def test_empty_data_read(self):
        read_data, _ =  self.data.getData(None, 0, False, None)
        self.failUnlessEqual(read_data.dtype, np.dtype(float))
        self.failUnlessEqual(read_data.size, 0)

if __name__ == '__main__':
    pytest.main(['-v', __file__])
