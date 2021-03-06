import math

from optuna.pruners.base import BasePruner
from optuna.structs import StudyDirection
from optuna import type_checking

if type_checking.TYPE_CHECKING:
    from typing import List  # NOQA

    from optuna.structs import FrozenTrial  # NOQA
    from optuna.study import Study  # NOQA


class SuccessiveHalvingPruner(BasePruner):
    """Pruner using Asynchronous Successive Halving Algorithm.

    `Successive Halving <https://arxiv.org/abs/1502.07943>`_ is a bandit-based algorithm to
    identify the best one among multiple configurations. This class implements an asynchronous
    version of Successive Halving. Please refer to the paper of
    `Asynchronous Successive Halving <http://arxiv.org/abs/1810.05934>`_ for detailed descriptions.

    Note that, this class does not take care of the parameter for the maximum
    resource, referred to as :math:`R` in the paper. The maximum resource allocated to a trial is
    typically limited inside the objective function (e.g., ``step`` number in `simple.py
    <https://github.com/optuna/optuna/tree/c5777b3e/examples/pruning/simple.py#L31>`_,
    ``EPOCH`` number in `chainer_integration.py
    <https://github.com/optuna/optuna/tree/c5777b3e/examples/pruning/chainer_integration.py#L65>`_).

    Example:

        We minimize an objective function with ``SuccessiveHalvingPruner``.

        .. code::

            >>> from optuna import create_study
            >>> from optuna.pruners import SuccessiveHalvingPruner
            >>>
            >>> def objective(trial):
            >>>     ...
            >>>
            >>> study = create_study(pruner=SuccessiveHalvingPruner())
            >>> study.optimize(objective)

    Args:
        min_resource:
            A parameter for specifying the minimum resource allocated to a trial
            (in the `paper <http://arxiv.org/abs/1810.05934>`_ this parameter is
            referred to as :math:`r`).

            A trial is never pruned until it executes
            :math:`\\mathsf{min}\\_\\mathsf{resource} \\times
            \\mathsf{reduction}\\_\\mathsf{factor}^{
            \\mathsf{min}\\_\\mathsf{early}\\_\\mathsf{stopping}\\_\\mathsf{rate}}`
            steps (i.e., the completion point of the first rung). When the trial completes
            the first rung, it will be promoted to the next rung only
            if the value of the trial is placed in the top
            :math:`{1 \\over \\mathsf{reduction}\\_\\mathsf{factor}}` fraction of
            the all trials that already have reached the point (otherwise it will be pruned there).
            If the trial won the competition, it runs until the next completion point (i.e.,
            :math:`\\mathsf{min}\\_\\mathsf{resource} \\times
            \\mathsf{reduction}\\_\\mathsf{factor}^{
            (\\mathsf{min}\\_\\mathsf{early}\\_\\mathsf{stopping}\\_\\mathsf{rate}
            + \\mathsf{rung})}` steps)
            and repeats the same procedure.
        reduction_factor:
            A parameter for specifying reduction factor of promotable trials
            (in the `paper <http://arxiv.org/abs/1810.05934>`_ this parameter is
            referred to as :math:`\\eta`).  At the completion point of each rung,
            about :math:`{1 \\over \\mathsf{reduction}\\_\\mathsf{factor}}`
            trials will be promoted.
        min_early_stopping_rate:
            A parameter for specifying the minimum early-stopping rate
            (in the `paper <http://arxiv.org/abs/1810.05934>`_ this parameter is
            referred to as :math:`s`).
    """

    def __init__(self, min_resource=1, reduction_factor=4, min_early_stopping_rate=0):
        # type: (int, int, int) -> None

        if min_resource < 1:
            raise ValueError('The value of `min_resource` is {}, '
                             'but must be `min_resource >= 1`'.format(min_resource))

        if reduction_factor < 2:
            raise ValueError('The value of `reduction_factor` is {}, '
                             'but must be `reduction_factor >= 2`'.format(reduction_factor))

        if min_early_stopping_rate < 0:
            raise ValueError(
                'The value of `min_early_stopping_rate` is {}, '
                'but must be `min_early_stopping_rate >= 0`'.format(min_early_stopping_rate))

        self._min_resource = min_resource
        self._reduction_factor = reduction_factor
        self._min_early_stopping_rate = min_early_stopping_rate

    def prune(self, study, trial):
        # type: (Study, FrozenTrial) -> bool

        step = trial.last_step
        if step is None:
            return False

        rung = _get_current_rung(trial)
        value = trial.intermediate_values[step]
        trials = None

        while True:
            rung_promotion_step = self._min_resource * \
                (self._reduction_factor ** (self._min_early_stopping_rate + rung))
            if step < rung_promotion_step:
                return False

            if math.isnan(value):
                return True

            if trials is None:
                trials = study.get_trials(deepcopy=False)

            rung_key = _completed_rung_key(rung)

            study._storage.set_trial_system_attr(trial._trial_id, rung_key, value)

            if not _is_trial_promotable_to_next_rung(
                    value, _get_competing_values(trials, value, rung_key),
                    self._reduction_factor, study.direction):
                return True

            rung += 1


def _get_current_rung(trial):
    # type: (FrozenTrial) -> int

    # The following loop takes `O(log step)` iterations.
    rung = 0
    while _completed_rung_key(rung) in trial.system_attrs:
        rung += 1
    return rung


def _completed_rung_key(rung):
    # type: (int) -> str

    return 'completed_rung_{}'.format(rung)


def _get_competing_values(trials, value, rung_key):
    # type: (List[FrozenTrial], float, str) -> List[float]

    competing_values = [t.system_attrs[rung_key] for t in trials if rung_key in t.system_attrs]
    competing_values.append(value)
    return competing_values


def _is_trial_promotable_to_next_rung(value, competing_values, reduction_factor, study_direction):
    # type: (float, List[float], int, StudyDirection) -> bool

    promotable_idx = (len(competing_values) // reduction_factor) - 1

    if promotable_idx == -1:
        # Optuna does not support suspending or resuming ongoing trials. Therefore, for the first
        # `eta - 1` trials, this implementation instead promotes the trial if its value is the
        # smallest one among the competing values.
        promotable_idx = 0

    competing_values.sort()
    if study_direction == StudyDirection.MAXIMIZE:
        return value >= competing_values[-(promotable_idx + 1)]
    return value <= competing_values[promotable_idx]
