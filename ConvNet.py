import numpy as np
import tensorflow as tf
import datetime
import DataIO
import os


class ConvNet:
    def __init__(self, net_config, train_config, net_id):
        self.net_config = net_config
        self.train_config = train_config
        self.layer_name = {}
        self.bias_name = {}
        self.layers = {}
        self.bias = {}
        self.best_layer = {}
        self.best_bias = {}
        self.assign_best_layer = {}
        self.assign_best_bias = {}
        self.net_id = net_id
        self.res_noise_power_dict = {}
        self.res_noise_pdf_dict = {}
        self.trade_off = 1.0  # total_loss = trade_off * noise_loss + (1-trade_off) * intf_loss

    def build_network(self, built_for_training=False):
        x_in = tf.placeholder(tf.float32, [None, self.net_config.feature_length], name='x_in')
        x_in_reshape = tf.reshape(x_in, (-1, self.net_config.feature_length, 1, 1), name='x_in_reshape')

        layer_output = {}

        for layer in range(self.net_config.conv_layers_num):
            self.layer_name[layer] = format("conv_layer%d" % layer)
            self.bias_name[layer] = format("b%d" % layer)

            if layer == 0:
                layer_input = x_in_reshape
                in_channels = 1

            else:
                layer_input = layer_output[layer - 1]
                in_channels = self.net_config.feature_map_nums[layer - 1]
            out_channels = self.net_config.feature_map_nums[layer]

            if built_for_training:
                # Xavier initialization for training
                self.layers[layer] = tf.get_variable(name=self.layer_name[layer],
                                                     shape=[self.net_config.filter_sizes[layer], 1, in_channels,
                                                            out_channels],
                                                     dtype=tf.float32,
                                                     initializer=tf.contrib.layers.xavier_initializer())
                self.bias[layer] = tf.get_variable(name=self.bias_name[layer], shape=[out_channels],
                                                   dtype=tf.float32, initializer=tf.contrib.layers.xavier_initializer())
                self.best_layer[layer] = tf.Variable(
                    tf.ones([self.net_config.filter_sizes[layer], 1, in_channels, out_channels], tf.float32),
                    dtype=tf.float32)
                self.best_bias[layer] = tf.Variable(tf.ones([out_channels], tf.float32), dtype=tf.float32)
                self.assign_best_layer[layer] = self.best_layer[layer].assign(self.layers[layer])
                self.assign_best_bias[layer] = self.best_bias[layer].assign(self.bias[layer])
            else:
                # just build tensors for testing and their values will be loaded later.
                self.layers[layer] = tf.Variable(
                    tf.random_normal([self.net_config.filter_sizes[layer], 1, in_channels, out_channels], 0, 1,
                                     tf.float32), dtype=tf.float32,
                    name=self.layer_name[layer])
                self.bias[layer] = tf.Variable(tf.random_normal([out_channels], 0, 1, tf.float32), dtype=tf.float32,
                                               name=self.bias_name[layer])

            layer_output[layer] = tf.nn.relu(
                tf.nn.conv2d(layer_input, self.layers[layer], [1, 1, 1, 1], 'SAME') + self.bias[layer])
            print("Layer %d (Conv.) shape:" % layer, np.shape(layer_output[layer]))

        layer_output[self.net_config.conv_layers_num - 1] = \
            tf.reshape(layer_output[self.net_config.conv_layers_num - 1], [-1, self.net_config.feature_length])
        print("Layer %d (Conv.) reshape:" % (self.net_config.conv_layers_num - 1),
              np.shape(layer_output[self.net_config.conv_layers_num - 1]))

        # Dense layer
        for layer in range(self.net_config.conv_layers_num, self.net_config.total_layers_num):
            self.layer_name[layer] = format("dense_layer%d" % layer)
            self.bias_name[layer] = format("b%d" % layer)

            layer_output[layer] = tf.layers.dense(inputs=layer_output[layer - 1], units=self.net_config.feature_length,
                                                  activation=tf.nn.relu, name=self.layer_name[layer])
            print("Layer %d (Dense) shape:" % layer, np.shape(layer_output[layer]))

        # Multiple task
        self.layer_name[self.net_config.total_layers_num] = "output_layer_noise"
        self.bias_name[self.net_config.total_layers_num] = "b_noise"
        y_out = tf.layers.dense(inputs=layer_output[self.net_config.total_layers_num - 1],
                                units=self.net_config.feature_length, name="output_layer_noise")
        y_out = tf.reshape(y_out, [-1, self.net_config.feature_length])

        self.layer_name[self.net_config.total_layers_num + 1] = "output_layer_intf"
        self.bias_name[self.net_config.total_layers_num + 1] = "b_intf"
        i_out = tf.layers.dense(inputs=layer_output[self.net_config.total_layers_num - 1], units=1,
                                name="output_layer_intf")
        i_out = tf.reshape(i_out, [-1, 1])

        print("CNN network built!")
        print("Noise output shape:", y_out.get_shape)
        print("Indicator output shape:", i_out.get_shape)

        return x_in, y_out, i_out

    def restore_network_with_model_id(self, sess_in, restore_layers_num, model_id):
        # restore some layers
        save_dict = {}
        if restore_layers_num > 0:
            for layer in range(restore_layers_num):
                save_dict[self.layer_name[layer]] = self.layers[layer]
                save_dict[self.bias_name[layer]] = self.bias[layer]
            model_id_str = np.array2string(model_id, separator='_', formatter={'int': lambda d: "%d" % d})
            model_id_str = model_id_str[1:(len(model_id_str) - 1)]
            model_folder = format("%snetid%d_model%s" % (self.net_config.model_folder, self.net_id, model_id_str))
            restore_model_name = format("%s/model.ckpt" % model_folder)
            saver_restore = tf.train.Saver(save_dict)
            saver_restore.restore(sess_in, restore_model_name)
            print("Restore the first %d layers.\n" % restore_layers_num)

    def get_res_noise_power(self, model_id):
        if self.res_noise_power_dict.__len__() == 0:

            # if len(model_id) > self.net_id+1, discard redundant parts.
            model_id_str = np.array2string(model_id[0:(self.net_id + 1)], separator='_',
                                           formatter={'int': lambda d: "%d" % d})
            model_id_str = model_id_str[1:(len(model_id_str) - 1)]
            residual_noise_power_file = format("%sresidual_noise_property_netid%d_model%s.txt" % (
                self.net_config.residual_noise_property_folder, self.net_id, model_id_str))
            data = np.loadtxt(residual_noise_power_file, dtype=np.float32)
            shape_data = np.shape(data)
            if np.size(shape_data) == 1:
                self.res_noise_power_dict[data[0]] = data[1:shape_data[0]]
            else:
                SNR_num = shape_data[0]
                for i in range(SNR_num):
                    self.res_noise_power_dict[data[i, 0]] = data[i, 1:shape_data[1]]
        return self.res_noise_power_dict

    def test_network_online(self, dataio, x_in, y_label, i_label, loss_after_training, yloss_after_training, iloss_after_training, sess_in):
        # this function is used to test the network loss online when training network
        remain_samples = self.train_config.test_sample_num
        load_batch_size = self.train_config.test_minibatch_size
        ave_loss_after_train = 0.0
        ave_yloss_after_train = 0.0
        ave_iloss_after_train = 0.0
        while remain_samples > 0:
            if remain_samples < self.train_config.test_minibatch_size:
                load_batch_size = remain_samples

            batch_xs, batch_ys, batch_i = dataio.load_batch_for_test(load_batch_size)  # features, labels

            loss_after_training_value = sess_in.run(loss_after_training,
                                                    feed_dict={x_in: batch_xs, y_label: batch_ys, i_label: batch_i})
            y_loss_value = sess_in.run(yloss_after_training,
                                       feed_dict={x_in: batch_xs, y_label: batch_ys, i_label: batch_i})
            i_loss_value = sess_in.run(iloss_after_training,
                                       feed_dict={x_in: batch_xs, y_label: batch_ys, i_label: batch_i})
            remain_samples -= load_batch_size
            ave_loss_after_train += loss_after_training_value * load_batch_size
            ave_yloss_after_train += y_loss_value * load_batch_size
            ave_iloss_after_train += i_loss_value * load_batch_size

        ave_loss_after_train /= np.double(self.train_config.test_sample_num)
        print(ave_loss_after_train, ave_yloss_after_train, ave_iloss_after_train)
        return ave_loss_after_train

    def save_network(self, sess_in, model_id):
        # save network
        save_dict = {}
        for layer in range(self.net_config.save_layers):
            save_dict[self.layer_name[layer]] = self.best_layer[layer]
            save_dict[self.bias_name[layer]] = self.best_bias[layer]

        model_id_str = np.array2string(model_id, separator='_', formatter={'int': lambda d: "%d" % d})
        model_id_str = model_id_str[1:(len(model_id_str) - 1)]
        save_model_folder = format("%snetid%d_model%s" % (self.net_config.model_folder, self.net_id, model_id_str))

        if not os.path.exists(save_model_folder):
            os.makedirs(save_model_folder)
        save_model_name = format("%s/model.ckpt" % save_model_folder)
        saver_save = tf.train.Saver(save_dict)
        saver_save.save(sess_in, save_model_name)
        print("Save %d layers.\n" % self.net_config.save_layers)

    def train_network(self, model_id):
        start = datetime.datetime.now()
        dataio_train = DataIO.TrainingDataIO(self.train_config.training_feature_file,
                                             self.train_config.training_noise_label_file,
                                             self.train_config.training_intf_label_file,
                                             self.train_config.training_sample_num,
                                             self.net_config.feature_length,
                                             self.net_config.noise_label_length)
        dataio_test = DataIO.TestDataIO(self.train_config.test_feature_file,
                                        self.train_config.test_noise_label_file,
                                        self.train_config.test_intf_label_file,
                                        self.train_config.test_sample_num,
                                        self.net_config.feature_length,
                                        self.net_config.noise_label_length)

        x_in, y_out, i_out = self.build_network(True)

        # Define loss function
        y_label = tf.placeholder(tf.float32, (None, self.net_config.noise_label_length), "y_label")
        i_label = tf.placeholder(tf.float32, (None, 1), "i_label")

        y_loss = tf.reduce_mean(tf.square(y_out - y_label))
        i_loss = tf.losses.sigmoid_cross_entropy(multi_class_labels=i_label, logits=i_out)

        total_loss = self.trade_off * y_loss + (1 - self.trade_off) * i_loss
        test_loss = total_loss
        test_y_loss = y_loss
        test_i_loss = i_loss

        # Stochastic Gradient Descent (SGD): Adam
        train_step = tf.train.AdamOptimizer().minimize(total_loss)

        # Initialize operation
        init = tf.global_variables_initializer()

        # Create a session and run initialization
        sess = tf.Session()
        sess.run(init)

        self.restore_network_with_model_id(sess, self.net_config.restore_layers, model_id)

        # # calculate the loss before training and assign it to min_loss
        min_loss = self.test_network_online(dataio_test, x_in, y_label, i_label, test_loss, sess)

        # self.save_network_temporarily(sess)

        # Training start
        count = 0
        epoch = 0
        print('Iteration\tLoss')

        while epoch < self.train_config.epoch_num:
            epoch += 1
            batch_xs, batch_ys, batch_i = dataio_train.load_next_minibatch(self.train_config.training_minibatch_size)
            sess.run([train_step], feed_dict={x_in: batch_xs, y_label: batch_ys, i_label: batch_i})

            if epoch % 500 == 0 or epoch == self.train_config.epoch_num:
                print(epoch)
                ave_loss_after_train = self.test_network_online(dataio_test, x_in, y_label, i_label,
                                                                test_loss, test_y_loss, test_i_loss, sess)
                if ave_loss_after_train < min_loss:
                    min_loss = ave_loss_after_train
                    # self.save_network_temporarily(sess)
                    count = 0
                else:
                    count += 1
                    if count >= 8:  # no patience
                        break

        self.save_network(sess, model_id)
        sess.close()
        end = datetime.datetime.now()
        print('Final minimum loss: %f' % min_loss)
        print('Used time for training: %ds' % (end - start).seconds)
