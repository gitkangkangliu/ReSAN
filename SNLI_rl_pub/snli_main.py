
import math, os

import tensorflow as tf

from configs import cfg
from snli_rl_log_analysis import do_analyse_snli_rl
from src.dataset import Dataset
from src.evaluator import Evaluator
from src.graph_handler import GraphHandler
from src.perform_recorder import PerformRecoder
from src.utils.file import load_file, save_file
from src.utils.record_log import _logger
from src.utils.time_counter import TimeCounter

# choose model
network_type = cfg.network_type
if network_type == 'hw_resan_base':
    from src.model.model_hw_resan_base import ModelHwResanBase as Model
elif network_type == 'hw_resan':
    from src.model.model_hw_resan import ModelHwResan as Model
elif network_type == 'hw_resan_exp':
    from src.model.model_hw_resan_exp import ModelHwResanExp as Model


is_base_training = network_type.endswith('base')

def train():
    output_model_params()
    loadFile = True
    ifLoad, data = False, None
    if loadFile:
        ifLoad, data = load_file(cfg.processed_path, 'processed data', 'pickle')
    if not ifLoad or not loadFile:
        train_data_obj = Dataset(cfg.train_data_path, 'train')
        dev_data_obj = Dataset(cfg.dev_data_path, 'dev', dicts=train_data_obj.dicts)
        test_data_obj = Dataset(cfg.test_data_path, 'test', dicts=train_data_obj.dicts)

        save_file({'train_data_obj': train_data_obj, 'dev_data_obj': dev_data_obj, 'test_data_obj': test_data_obj},
                  cfg.processed_path)

        train_data_obj.save_dict(cfg.dict_path)
    else:
        train_data_obj = data['train_data_obj']
        dev_data_obj = data['dev_data_obj']
        test_data_obj = data['test_data_obj']

    train_data_obj.filter_data()
    dev_data_obj.filter_data()
    test_data_obj.filter_data()

    emb_mat_token, emb_mat_glove = train_data_obj.emb_mat_token, train_data_obj.emb_mat_glove

    with tf.variable_scope(cfg.base_name) as scope:
        model = Model(emb_mat_token, emb_mat_glove, len(train_data_obj.dicts['token']),
                      len(train_data_obj.dicts['char']), train_data_obj.max_lens['token'], scope.name)
    graphHandler = GraphHandler(model)
    evaluator = Evaluator(model)
    performRecoder = PerformRecoder(cfg.save_num)

    if cfg.gpu_mem is None:
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.gpu_mem,
                                    allow_growth=True)
        graph_config = tf.ConfigProto(gpu_options=gpu_options, allow_soft_placement=True)

    elif cfg.gpu_mem < 1.:
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.gpu_mem)
        graph_config = tf.ConfigProto(gpu_options=gpu_options)
    else:
        gpu_options = tf.GPUOptions()
        graph_config = tf.ConfigProto(gpu_options=gpu_options)

    sess = tf.Session(config=graph_config)
    graphHandler.initialize(sess)

    # begin training
    steps_per_epoch = int(math.ceil(1.0 * train_data_obj.sample_num / cfg.train_batch_size))
    num_steps = cfg.num_steps or steps_per_epoch * cfg.max_epoch

    global_step = 0

    for sample_batch, batch_num, data_round, idx_b in train_data_obj.generate_batch_sample_iter(num_steps):
        global_step = sess.run(model.global_step) + 1
        if_get_summary = global_step % (cfg.log_period or steps_per_epoch) == 0
        loss, summary = model.step(sess, sample_batch, get_summary=if_get_summary, global_step_value=global_step)
        if global_step % 100 == 0:
            _logger.add('data round: %d: %d/%d, global step:%d -- loss_sl: %.4f, loss_rl: %.4f' %
                        (data_round, idx_b, batch_num, global_step, loss[0], loss[1]))

        if if_get_summary:
            graphHandler.add_summary(summary, global_step)

        # Occasional evaluation
        evaluation = False
        if cfg.model_dir_suffix == 'test':
            if global_step % (cfg.eval_period or steps_per_epoch) == 0:
                evaluation = True
        elif is_base_training:
            if global_step > cfg.num_steps - 350000 and (global_step % (cfg.eval_period or steps_per_epoch) == 0):
                evaluation = True
        else:
            if global_step % (cfg.eval_period or steps_per_epoch) == 0:
                if cfg.load_model:
                    evaluation = True
                else:
                    if global_step > 250000:
                        evaluation = True
        if evaluation:
            # ---- dev ----
            dev_loss, dev_accu, dev_perc = evaluator.get_evaluation(
                sess, dev_data_obj, global_step
            )
            _logger.add('==> for dev, loss: %.4f %.4f, perc: %.4f, accuracy: %.4f' %
                        (dev_loss[0],dev_loss[1], dev_perc, dev_accu))
            # ---- test ----
            test_loss, test_accu, test_perc = evaluator.get_evaluation(
                sess, test_data_obj, global_step
            )
            _logger.add('~~> for test, loss: %.4f %.4f, perc: %.4f, accuracy: %.4f' %
                        (test_loss[0], test_loss[1], test_perc, test_accu))

            is_in_top, deleted_step = performRecoder.update_top_list(global_step, dev_accu, sess)

        this_epoch_time, mean_epoch_time = cfg.time_counter.update_data_round(data_round)
        if this_epoch_time is not None and mean_epoch_time is not None:
            _logger.add('##> this epoch time: %f, mean epoch time: %f' % (this_epoch_time, mean_epoch_time))

        if is_base_training and global_step >= 200000 and global_step % 50000 == 0 and cfg.save_model:
            graphHandler.save(sess, global_step)

    _logger.writeToFile()
    do_analyse_snli_rl(_logger.path)


def test():

    assert cfg.load_path is not None
    output_model_params()
    loadFile = True
    ifLoad, data = False, None
    if loadFile:
        ifLoad, data = load_file(cfg.processed_path, 'processed data', 'pickle')
    if not ifLoad or not loadFile:
        train_data_obj = Dataset(cfg.train_data_path, 'train')
        dev_data_obj = Dataset(cfg.dev_data_path, 'dev', dicts=train_data_obj.dicts)
        test_data_obj = Dataset(cfg.test_data_path, 'test', dicts=train_data_obj.dicts)

        save_file({'train_data_obj': train_data_obj, 'dev_data_obj': dev_data_obj, 'test_data_obj': test_data_obj},
                  cfg.processed_path)

        train_data_obj.save_dict(cfg.dict_path)
    else:
        train_data_obj = data['train_data_obj']
        dev_data_obj = data['dev_data_obj']
        test_data_obj = data['test_data_obj']

    train_data_obj.filter_data()
    dev_data_obj.filter_data()
    test_data_obj.filter_data()

    emb_mat_token, emb_mat_glove = train_data_obj.emb_mat_token, train_data_obj.emb_mat_glove

    with tf.variable_scope(cfg.base_name) as scope:
        model = Model(emb_mat_token, emb_mat_glove, len(train_data_obj.dicts['token']),
                      len(train_data_obj.dicts['char']), train_data_obj.max_lens['token'], scope.name)
    graphHandler = GraphHandler(model)
    evaluator = Evaluator(model)

    if cfg.gpu_mem is None:
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.gpu_mem,
                                    allow_growth=True)
        graph_config = tf.ConfigProto(gpu_options=gpu_options, allow_soft_placement=True)

    elif cfg.gpu_mem < 1.:
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.gpu_mem)
        graph_config = tf.ConfigProto(gpu_options=gpu_options)
    else:
        gpu_options = tf.GPUOptions()
        graph_config = tf.ConfigProto(gpu_options=gpu_options)

    sess = tf.Session(config=graph_config)
    graphHandler.initialize(sess)

    # todo: test model
    # ---- dev ----
    dev_loss, dev_accu, dev_perc = evaluator.get_evaluation(
        sess, dev_data_obj, None
    )
    _logger.add('==> for dev, loss: %.4f %.4f, perc: %.4f, accuracy: %.4f' %
                (dev_loss[0], dev_loss[1], dev_perc, dev_accu))
    # ---- test ----
    test_loss, test_accu, test_perc = evaluator.get_evaluation(
        sess, test_data_obj, None
    )
    _logger.add('~~> for test, loss: %.4f %.4f, perc: %.4f, accuracy: %.4f' %
                (test_loss[0], test_loss[1], test_perc, test_accu))

    # ---- train ----
    train_loss, train_accu, train_perc = evaluator.get_evaluation(
        sess, train_data_obj, None
    )
    _logger.add('--> for train, loss: %.4f %.4f, perc: %.4f, accuracy: %.4f' %
                (train_loss[0], train_loss[1], train_perc, train_accu))

def multi_test():
    assert cfg.load_path is not None
    output_model_params()
    loadFile = True
    ifLoad, data = False, None
    if loadFile:
        ifLoad, data = load_file(cfg.processed_path, 'processed data', 'pickle')
    if not ifLoad or not loadFile:
        train_data_obj = Dataset(cfg.train_data_path, 'train')
        dev_data_obj = Dataset(cfg.dev_data_path, 'dev', dicts=train_data_obj.dicts)
        test_data_obj = Dataset(cfg.test_data_path, 'test', dicts=train_data_obj.dicts)

        save_file({'train_data_obj': train_data_obj, 'dev_data_obj': dev_data_obj, 'test_data_obj': test_data_obj},
                  cfg.processed_path)

        train_data_obj.save_dict(cfg.dict_path)
    else:
        train_data_obj = data['train_data_obj']
        dev_data_obj = data['dev_data_obj']
        test_data_obj = data['test_data_obj']

    train_data_obj.filter_data()
    dev_data_obj.filter_data()
    test_data_obj.filter_data()

    emb_mat_token, emb_mat_glove = train_data_obj.emb_mat_token, train_data_obj.emb_mat_glove

    with tf.variable_scope(cfg.base_name) as scope:
        model = Model(emb_mat_token, emb_mat_glove, len(train_data_obj.dicts['token']),
                      len(train_data_obj.dicts['char']), train_data_obj.max_lens['token'], scope.name)
    graphHandler = GraphHandler(model)
    evaluator = Evaluator(model)

    if cfg.gpu_mem is None:
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.gpu_mem,
                                    allow_growth=True)
        graph_config = tf.ConfigProto(gpu_options=gpu_options, allow_soft_placement=True)

    elif cfg.gpu_mem < 1.:
        gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=cfg.gpu_mem)
        graph_config = tf.ConfigProto(gpu_options=gpu_options)
    else:
        gpu_options = tf.GPUOptions()
        graph_config = tf.ConfigProto(gpu_options=gpu_options)

    sess = tf.Session(config=graph_config)
    graphHandler.initialize(sess)

    repeat_num = 10
    time_counter = TimeCounter()

    for t in range(repeat_num):
        # ---- dev ----
        test_loss, test_accu, test_perc = evaluator.get_evaluation(
            sess, test_data_obj, None, time_counter=time_counter
        )
        _logger.add('==> for test, loss: %.4f %.4f, perc: %.4f, accuracy: %.4f' %
                    (test_loss[0], test_loss[1], test_perc, test_accu))
        print(time_counter.update_data_round(t+1))


def main(_):
    if cfg.mode == 'train':
        train()
    elif cfg.mode == 'test':
        test()
    elif cfg.mode == 'multi_test':
        multi_test()
    else:
        raise RuntimeError('no running mode named as %s' % cfg.mode)


def output_model_params():
    _logger.add()
    _logger.add('==>model_title: ' + cfg.model_name[1:])
    _logger.add()
    for key,value in cfg.args.__dict__.items():
        if key not in ['test','shuffle']:
            _logger.add('%s: %s' % (key, value))


if __name__ == '__main__':
    tf.app.run()