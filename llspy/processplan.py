from llspy.llsdir import LLSdir
from llspy.imgprocessors import ImgProcessor, ImgWriter, CUDADeconProcessor
from llspy.libcudawrapper import cuda_reset
from llspy.otf import choose_otf
from llspy.libcudawrapper import RLContext


class ProcessPlan(object):

    def __init__(self, llsdir, imps=[], t_range=None, c_range=None):
        if not isinstance(llsdir, LLSdir):
            raise ValueError('First argument to ProcessPlan must be an LLSdir')
        assert isinstance(imps, (list, tuple)), 'imps argument must be a '\
                                                'list or tuple'
        for imp in imps:
            if not issubclass(imp[0], ImgProcessor):
                raise ValueError('imp item "{}" is not an ImgProcessor'
                                 .format(imp))
        self.llsdir = llsdir
        self.imp_classes = imps
        self.t_range = t_range or list(range(llsdir.params.nt))
        self.c_range = c_range or list(range(llsdir.params.nc))

    @property
    def ready(self):
        return hasattr(self, 'imps') and len(self.imps)

    def check_sanity(self):
        # sanity checkes go here...
        warnings = []
        writers = [issubclass(p[0], ImgWriter) for p in self.imp_classes]
        if not any(writers):
            warnings.append('No Image writer/output detected.')
        try:
            idx_of_last_writer = list(reversed(writers)).index(True)
        except ValueError:
            idx_of_last_writer = False
        if idx_of_last_writer:
            warnings.append('You have image processors after the last Writer')

        if warnings:
            raise self.PlanWarning("\n".join(warnings))

    def plan(self, skip_warnings=False):
        if not skip_warnings:
            self.check_sanity()

        errors = []
        self.imps = []  # will hold instantiated imps
        for imp_tup in self.imp_classes:
            imp, params, active = imp_tup[:3]
            if not active:
                continue
            try:
                self.imps.append(imp.from_llsdir(self.llsdir, **params))
            except imp.ImgProcessorError as e:
                errors.append('%s:  ' % imp.name() + str(e))

        if errors:
            # FIXME: should probably only clobber broken imps, not all imps
            self.imps = []
            raise self.PlanError("Please fix the following errors:\n\n" +
                                 "\n\n".join(errors))

    def execute(self):
        decons = [isinstance(i, CUDADeconProcessor) for i in self.imps]
        has_decon = any(decons)
        meta = {
            'c': self.c_range,
            'w': [self.llsdir.params.wavelengths[i] for i in self.c_range],
            'params': self.llsdir.params,
            'has_background': True,
        }
        for t in self.t_range:
            data = self.llsdir.data.asarray(t=t, c=self.c_range)
            meta['axes'] = data.axes
            meta['t'] = t
            if len(self.c_range) == 1 and has_decon:
                wave = meta['w'][0]
                otf_dir = self.imps[decons.index(True)].otf_dir
                width = self.imps[decons.index(True)].width
                otf = choose_otf(wave, otf_dir, meta['params'].date,
                                 meta['params'].mask)
                with RLContext(data.shape, otf, meta['params'].dz,
                               deskew=meta['params'].deskew,
                               width=width) as ctx:
                    meta['out_shape'] = ctx.out_shape
                    for imp in self.imps:
                        data, meta = imp(data, meta)
            else:
                for imp in self.imps:
                    data, meta = imp(data, meta)

        cuda_reset()

    class PlanError(Exception):
        """ hard error if the plan cannot be executed as requested """
        pass

    class PlanWarning(Exception):
        """ light error, if a plan has ill-advised steps """
        pass
