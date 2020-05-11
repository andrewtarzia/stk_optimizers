"""
XTB Optimizers
==============

#. :class:`.XTB`

Wrappers for optimizers within the :mod:`xtb` code.

"""

import logging
import os
import shutil
import uuid
import subprocess as sp

from .optimizers import Optimizer
from ..utilities import (
    is_valid_xtb_solvent,
    XTBInvalidSolventError,
    XTBExtractor
)

logger = logging.getLogger(__name__)


class XTBOptimizerError(Exception):
    ...


class XTBConvergenceError(XTBOptimizerError):
    ...


class XTB(Optimizer):
    """
    Uses GFN-xTB [1]_ to optimize molecules.

    Notes
    -----
    When running :meth:`optimize`, this calculator changes the
    present working directory with :func:`os.chdir`. The original
    working directory will be restored even if an error is raised, so
    unless multi-threading is being used this implementation detail
    should not matter.

    If multi-threading is being used an error could occur if two
    different threads need to know about the current working directory
    as :class:`.XTB` can change it from under them.

    Note that this does not have any impact on multi-processing,
    which should always be safe.

    Furthermore, :meth:`optimize` will check that the
    structure is adequately optimized by checking for negative
    frequencies after a Hessian calculation. `max_runs` can be
    provided to the initializer to set the maximum number of
    optimizations which will be attempted at the given
    `opt_level` to obtain an optimized structure. However, we outline
    in the examples how to iterate over `opt_levels` to increase
    convergence criteria and hopefully obtain an optimized structure.
    The presence of negative frequencies can occur even when the
    optimization has converged based on the given `opt_level`.

    Attributes
    ----------
    incomplete : :class:`set` of :class:`.Molecule`
        A :class:`set` of molecules passed to :meth:`optimize` whose
        optimzation was incomplete.

    Examples
    --------
    Note that for :class:`.ConstructedMolecule` objects constructed by
    ``stk``, :class:`XTB` should usually be used in a
    :class:`.OptimizerSequence`. This is because xTB only uses
    xyz coordinates as input and so will not recognize the long bonds
    created during construction. An optimizer which can minimize
    these bonds should be used before :class:`XTB`.

    .. code-block:: python

        import stk
        import stko

        bb1 = stk.BuildingBlock('NCCNCCN', [stk.PrimaryAminoFactory()])
        bb2 = stk.BuildingBlock('O=CCCC=O', [stk.AldehydeFactory()])
        polymer = stk.ConstructedMolecule(
            stk.polymer.Linear(
                building_blocks=(bb1, bb2),
                repeating_unit="AB",
                orientations=[0, 0],
                num_repeating_units=1
            )
        )

        xtb = stko.OptimizerSequence(
            stko.UFF(),
            stko.XTB(xtb_path='/opt/gfnxtb/xtb', unlimited_memory=True)
        )
        polymer = xtb.optimize(polymer)


    By default, all optimizations with xTB are performed using the
    ``--ohess`` flag, which forces the calculation of a numerical
    Hessian, thermodynamic properties and vibrational frequencies.
    :meth:`optimize` will check that the structure is appropriately
    optimized (i.e. convergence is obtained and no negative vibrational
    frequencies are present) and continue optimizing a structure (up to
    `max_runs` times) until this is achieved. This loop, by
    default, will be performed at the same `opt_level`. The
    following example shows how a user may optimize structures with
    tigher convergence criteria (i.e. different `opt_level`)
    until the structure is sufficiently optimized. Furthermore, the
    calculation of the Hessian can be turned off using
    `max_runs` to ``1`` and `calculate_hessian` to ``False``.

    .. code-block:: python

        # Use crude optimization with max_runs=1 because this will
        # not achieve optimization and rerunning it is unproductive.
        xtb_crude = stko.XTB(
            xtb_path='/opt/gfnxtb/xtb',
            output_dir='xtb_crude',
            unlimited_memory=True,
            opt_level='crude',
            max_runs=1,
            calculate_hessian=True
        )
        # Use normal optimization with max_runs == 2.
        xtb_normal = stko.XTB(
            xtb_path='/opt/gfnxtb/xtb',
            output_dir='xtb_normal',
            unlimited_memory=True,
            opt_level='normal',
            max_runs=2
        )
        # Use vtight optimization with max_runs == 2, which should
        # achieve sufficient optimization.
        xtb_vtight = stko.XTB(
            xtb_path='/opt/gfnxtb/xtb',
            output_dir='xtb_vtight',
            unlimited_memory=True,
            opt_level='vtight',
            max_runs=2
        )

        optimizers = [xtb_crude, xtb_normal, xtb_vtight]
        for optimizer in optimizers:
            polymer = optimizer.optimize(polymer)
            if polymer not in optimizer.incomplete:
                break

    References
    ----------
    .. [1] https://xtb-docs.readthedocs.io/en/latest/setup.html

    """

    def __init__(
        self,
        xtb_path,
        gfn_version=2,
        output_dir=None,
        opt_level='normal',
        max_runs=2,
        calculate_hessian=True,
        num_cores=1,
        electronic_temperature=300,
        solvent=None,
        solvent_grid='normal',
        charge=0,
        num_unpaired_electrons=0,
        unlimited_memory=False,
    ):
        """
        Initialize a :class:`XTB` instance.

        Parameters
        ----------
        xtb_path : :class:`str`
            The path to the xTB executable.

        gfn_version : :class:`int`, optional
            Parameterization of GFN to use in xTB.
            For details see
            https://xtb-docs.readthedocs.io/en/latest/basics.html.

        output_dir : :class:`str`, optional
            The name of the directory into which files generated during
            the optimization are written, if ``None`` then
            :func:`uuid.uuid4` is used.

        opt_level : :class:`str`, optional
            Optimization level to use.
            Can be one of ``'crude'``, ``'sloppy'``, ``'loose'``,
            ``'lax'``, ``'normal'``, ``'tight'``, ``'vtight'``
            or ``'extreme'``.
            For details see
            https://xtb-docs.readthedocs.io/en/latest/optimization.html
            .

        max_runs : :class:`int`, optional
            Maximum number of optimizations to attempt in a row.

        calculate_hessian : :class:`bool`, optional
            Toggle calculation of the hessian and vibrational
            frequencies after optimization. ``True`` is required to
            check that the structure is completely optimized.
            ``False`` will drastically speed up the calculation but
            potentially provide incomplete optimizations and forces
            :attr:`max_runs` to be ``1``.

        num_cores : :class:`int`, optional
            The number of cores xTB should use.

        electronic_temperature : :class:`int`, optional
            Electronic temperature in Kelvin.

        solvent : :class:`str`, optional
            Solvent to use in GBSA implicit solvation method.
            For details see
            https://xtb-docs.readthedocs.io/en/latest/gbsa.html.

        solvent_grid : :class:`str`, optional
            Grid level to use in SASA calculations for GBSA implicit
            solvent.
            Can be one of ``'normal'``, ``'tight'``, ``'verytight'``
            or ``'extreme'``.
            For details see
            https://xtb-docs.readthedocs.io/en/latest/gbsa.html.

        charge : :class:`int`, optional
            Formal molecular charge.

        num_unpaired_electrons : :class:`int`, optional
            Number of unpaired electrons.

        unlimited_memory : :class: `bool`, optional
            If ``True`` :meth:`optimize` will be run without
            constraints on the stack size. If memory issues are
            encountered, this should be ``True``, however this may
            raise issues on clusters.

        """

        if solvent is not None:
            solvent = solvent.lower()
            if gfn_version == 0:
                raise XTBInvalidSolventError(
                    f'No solvent valid for version',
                    f' {gfn_version!r}.'
                )
            if not is_valid_xtb_solvent(gfn_version, solvent):
                raise XTBInvalidSolventError(
                    f'Solvent {solvent!r} is invalid for ',
                    f'version {gfn_version!r}.'
                )

        if not calculate_hessian and max_runs != 1:
            max_runs = 1
            logger.warning(
                'Requested that hessian calculation was skipped '
                'but the number of optimizations requested was '
                'greater than 1. The number of optimizations has been '
                'set to 1.'
            )

        self._xtb_path = xtb_path
        self._gfn_version = str(gfn_version)
        self._output_dir = output_dir
        self._opt_level = opt_level
        self._max_runs = max_runs
        self._calculate_hessian = calculate_hessian
        self._num_cores = str(num_cores)
        self._electronic_temperature = str(electronic_temperature)
        self._solvent = solvent
        self._solvent_grid = solvent_grid
        self._charge = str(charge)
        self._num_unpaired_electrons = str(num_unpaired_electrons)
        self._unlimited_memory = unlimited_memory
        self.incomplete = set()

    def _has_neg_frequencies(self, output_file):
        """
        Check for negative frequencies.

        Parameters
        ----------
        output_file : :class:`str`
            Name of output file with xTB results.

        Returns
        -------
        :class:`bool`
            Returns ``True`` if a negative frequency is present.

        """
        xtbext = XTBExtractor(output_file=output_file)
        # Check for one negative frequency, excluding the first
        # 6 frequencies.
        return any(x < 0 for x in xtbext.frequencies[6:])

    def _is_complete(self, output_file):
        """
        Check if xTB optimization has completed and converged.

        Parameters
        ----------
        output_file : :class:`str`
            Name of xTB output file.

        Returns
        -------
        :class:`bool`
            Returns ``False`` if a negative frequency is present.

        Raises
        -------
        :class:`XTBOptimizerError`
            If the optimization failed.

        :class:`XTBConvergenceError`
            If the optimization did not converge.

        """
        if output_file is None:
            # No simulation has been run.
            return False
        # If convergence is achieved, then .xtboptok should exist.
        if os.path.exists('.xtboptok'):
            # Check for negative frequencies in output file if the
            # hessian was calculated.
            # Return True if there exists at least one.
            if self._calculate_hessian:
                return not self._has_neg_frequencies(output_file)
            else:
                return True
        elif os.path.exists('NOT_CONVERGED'):
            raise XTBConvergenceError('Optimization not converged.')
        else:
            raise XTBOptimizerError('Optimization failed to complete')

    def _run_xtb(self, xyz, out_file):
        """
        Run GFN-xTB.

        Parameters
        ----------
        xyz : :class:`str`
            The name of the input structure ``.xyz`` file.

        out_file : :class:`str`
            The name of output file with xTB results.

        Returns
        -------
        None : :class:`NoneType`

        """

        # Modify the memory limit.
        if self._unlimited_memory:
            memory = 'ulimit -s unlimited ;'
        else:
            memory = ''

        # Set optimization level and type.
        if self._calculate_hessian:
            # Do optimization and check hessian.
            optimization = f'--ohess {self._opt_level}'
        else:
            # Do optimization.
            optimization = f'--opt {self._opt_level}'

        if self._solvent is not None:
            solvent = f'--gbsa {self._solvent} {self._solvent_grid}'
        else:
            solvent = ''

        cmd = (
            f'{memory} {self._xtb_path} {xyz} '
            f'--gfn {self._gfn_version} '
            f'{optimization} --parallel {self._num_cores} '
            f'--etemp {self._electronic_temperature} '
            f'{solvent} --chrg {self._charge} '
            f'--uhf {self._num_unpaired_electrons}'
        )

        with open(out_file, 'w') as f:
            # Note that sp.call will hold the program until completion
            # of the calculation.
            sp.call(
                cmd,
                stdin=sp.PIPE,
                stdout=f,
                stderr=sp.PIPE,
                # Shell is required to run complex arguments.
                shell=True
            )

    def _run_optimizations(self, mol):
        """
        Run loop of optimizations on `mol` using xTB.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        Returns
        -------
        mol : :class:`.Molecule`
            The optimized molecule.

        opt_complete : :class:`bool`
            Returns ``True`` if the calculation is complete and
            ``False`` if the calculation is incomplete.

        """

        for run in range(self._max_runs):
            xyz = f'input_structure_{run+1}.xyz'
            out_file = f'optimization_{run+1}.output'
            mol.write(xyz)
            self._run_xtb(xyz=xyz, out_file=out_file)
            # Check if the optimization is complete.
            coord_file = 'xtbhess.coord'
            coord_exists = os.path.exists(coord_file)
            output_xyz = 'xtbopt.xyz'
            opt_complete = self._is_complete(out_file)
            if not opt_complete:
                if coord_exists:
                    # The calculation is incomplete.
                    # Update mol from xtbhess.coord and continue.
                    mol = mol.with_structure_from_file(coord_file)
                else:
                    # Update mol from xtbopt.xyz.
                    mol = mol.with_structure_from_file(output_xyz)
                    # If the negative frequencies are small, then GFN
                    # may not produce the restart file. If that is the
                    # case, exit optimization loop and warn.
                    self.incomplete.add(mol)
                    logging.warning(
                        f'Small negative frequencies present in {mol}.'
                    )
                    return False
            else:
                # Optimization is complete.
                # Update mol from xtbopt.xyz.
                mol = mol.with_structure_from_file(output_xyz)
                break

        return mol, opt_complete

    def optimize(self, mol):
        """
        Optimize `mol`.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        Returns
        -------
        None : :class:`NoneType`

        """

        # Remove mol from self.incomplete if present.
        if mol in self.incomplete:
            self.incomplete.remove(mol)

        if self._output_dir is None:
            output_dir = str(uuid.uuid4().int)
        else:
            output_dir = self._output_dir
        output_dir = os.path.abspath(output_dir)

        if os.path.exists(output_dir):
            shutil.rmtree(output_dir)

        os.mkdir(output_dir)
        init_dir = os.getcwd()
        os.chdir(output_dir)

        try:
            mol, complete = self._run_optimizations(mol)
        finally:
            os.chdir(init_dir)

        if not complete:
            self.incomplete.add(mol)
            logging.warning(f'Optimization is incomplete for {mol}.')

        return mol
