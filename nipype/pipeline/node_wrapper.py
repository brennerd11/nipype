"""
Wraps interfaces modules to work with pipeline engine
"""
import os
import hashlib
from nipype.utils.filemanip import (copyfiles,fname_presuffix, cleandir,
                                    filename_to_list, list_to_filename)
from nipype.interfaces.base import Bunch, InterfaceResult
from tempfile import mkdtemp
from copy import deepcopy

class NodeWrapper(object):
    """
    Base class wrapper for interface objects to create nodes that can
    be used in the pipeline engine.

    Parameters
    ----------
    interface : interface object
        node specific interface  (fsl.Bet(), spm.Coregister())
    iterables : generator
        key and items to iterate
        for example to iterate over different frac values in fsl.Bet()
        node.iterables = dict(frac=lambda:[0.5,0.6,0.7])
    base_directory : directory
        base output directory (will be hashed before creations)
        default=None, which results in the use of mkdtemp
    diskbased : Boolean
        Whether the underlying object requires disk space for
        operation and storage of output
    overwrite : Boolean
        Whether to overwrite contents of output directory if it
        already exists. If directory exists and hash matches it
        assumes that process has been executed
    name : string
        Name of this node. By default node is named modulename.classname
    
    Notes
    -----
    creates output directory (hashname)
    discover files to work with
    renames them with hash
    moves hashed_files to hashed_output_directory

    Examples
    --------
    >>> import nipype.interfaces.spm as spm
    >>> realign = NodeWrapper(interface=spm.Realign(), base_directory='test2', diskbased=True)
    >>> realign.inputs.infile = os.path.abspath('data/funcrun.nii')
    >>> realign.inputs.register_to_mean = True
    >>> realign.run()

    """
    def __init__(self, interface=None,
                 iterables={}, iterfield=[],
                 diskbased=False, base_directory=None,
                 overwrite=False,
                 name=None):
        # interface can only be set at initialization
        if interface is None:
            raise Exception('Interface must be provided')
        self._interface  = interface
        self._result     = None
        self.iterables  = iterables
        self.iterfield  = iterfield
        self.parameterization = None
        self.disk_based = diskbased
        self.output_directory_base  = None
        self.overwrite = None
        if not self.disk_based:
            if base_directory is not None:
                msg = 'Setting base_directory requires a disk based interface'
                raise ValueError(msg)
        if self.disk_based:
            self.output_directory_base  = base_directory
            self.overwrite = overwrite
        if name is None:
            cname = interface.__class__.__name__
            mname = interface.__class__.__module__.split('.')[-1]
            self.name = '.'.join((cname, mname))
        else:
            self.name = name

    @property
    def interface(self):
        return self._interface
    
    @property
    def result(self):
        return self._result

    @property
    def inputs(self):
        return self._interface.inputs

    def set_input(self, parameter, val, *args, **kwargs):
        if callable(val):
            self._interface.inputs[parameter] = deepcopy(val(*args, **kwargs))
        else:
            self._interface.inputs[parameter] = deepcopy(val)

    def get_output(self, parameter):
        if self._result is not None:
            return self._result.outputs[parameter]
        else:
            return None
        
    def run(self):
        """Executes an interface within a directory.
        """
        # check to see if output directory and hash exist
        if self.disk_based:
            try:
                outdir = self._output_directory()
                outdir = self._make_output_dir(outdir)
            except:
                # XXX Should not catch bare exceptions!
                # Change this to catch a specific exception and raise an error.
                print "directory %s exists\n" % outdir
            self._interface.inputs.cwd = outdir
            hashvalue = self.hash_inputs()
            inputstr  = str(self.inputs)
            hashfile = os.path.join(outdir, '_0x%s.txt' % hashvalue)
            if self.overwrite or not os.path.exists(hashfile):
                print "continuing to execute\n"
                cleandir(outdir)
                # copy files over and change the inputs
                for info in self._interface.get_input_info():
                    files = self.inputs[info.key]
                    if files is not None:
                        infiles = filename_to_list(files)
                        newfiles = copyfiles(infiles, [outdir], copy=info.copy)
                        self.inputs[info.key] = list_to_filename(newfiles)
                self._run_interface(execute=True)
                if type(self._result.runtime) == list:
                    # XXX In what situation is runtime ever a list?
                    # Normally it's a Bunch.
                    returncode = 0
                    for r in self._result.runtime:
                        returncode = max(r.returncode, returncode)
                else:
                    returncode = self._result.runtime.returncode
                if returncode == 0:
                    try:
                        fd = open(hashfile, "wt")
                        fd.writelines(inputstr)
                        fd.close()
                    except IOError:
                        print "Unable to open the file in readmode:", hashfile
                else:
                    msg = "Could not run %s" % self.name
                    msg += "\nwith inputs:\n%s" % self.inputs
                    msg += "\n\tstderr: %s" % self._result.runtime.stderr
                    raise StandardError(msg)
            else:
                print "skipping\n"
                # change the inputs
                for info in self._interface.get_input_info():
                    files = self.inputs[info.key]
                    if files is not None:
                        if type(files) is not type([]):
                            infiles = [files]
                        else:
                            infiles = files
                        for i,f in enumerate(infiles):
                            newfile = fname_presuffix(f, newpath=outdir)
                            if not os.path.exists(newfile):
                                copyfiles(f, newfile, copy=info.copy)
                            if type(files) is not type([]):
                                self.inputs[info.key] = newfile
                            else:
                                self.inputs[info.key][i] = newfile
                self._run_interface(execute=False)
        else:
            self._run_interface(execute=True)
        if self.disk_based:
            # Should pickle the output
            pass
        return self._result

    def _run_interface(self, execute=True):
        if len(self.iterfield) == 1:
            itervals = self.inputs[self.iterfield[0]]
            notlist = False
            if type(itervals) is not type([]):
                notlist = True
                itervals = [itervals]
            self._result = InterfaceResult(interface=[], runtime=[],
                                           outputs=Bunch())
            for i,v in enumerate(itervals):
                print "running %s on %s\n"%(self.name, self.iterfield[0])
                self.set_input(self.iterfield[0], v)
                if execute:
                    result = self._interface.run()
                    self._result.interface.insert(i, result.interface)
                    self._result.runtime.insert(i, result.runtime)
                    outputs = result.outputs
                else:
                    outputs = self._interface.aggregate_outputs()
                for key,val in outputs.iteritems():
                    try:
                        self._result.outputs[key].insert(i, val)
                    except:
                        self._result.outputs[key] = []
                        self._result.outputs[key].insert(i, val)
            print itervals
            if notlist:
                self.set_input(self.iterfield[0], itervals.pop())
            else:
                self.set_input(self.iterfield[0], itervals)
        else:
            if execute:
                self._result = self._interface.run()
            else:
                aggouts = self._interface.aggregate_outputs()
                self._result = InterfaceResult(interface=None,
                                               runtime=None,
                                               outputs=aggouts)
        
    def update(self, **opts):
        self.inputs.update(**opts)
        
    def hash_inputs(self):
        """Computes a hash of the input fields of the underlying interface."""
        return hashlib.md5(str(self.inputs)).hexdigest()

    def _output_directory(self):
        if self.output_directory_base is None:
            self.output_directory_base = mkdtemp()
        return os.path.abspath(os.path.join(self.output_directory_base,
                                            self.name))

    def _make_output_dir(self, outdir):
        """Make the output_dir if it doesn't exist, else raise an exception
        """
        odir = os.path.abspath(outdir)
        if os.path.exists(outdir):
            raise IOError('Directory %s exists' % outdir)
        os.mkdir(outdir)
        return outdir

    def __repr__(self):
        return self.name

