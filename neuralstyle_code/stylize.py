import vgg

import tensorflow as tf
import numpy as np

from sys import stderr

CONTENT_LAYER = 'relu4_2'
STYLE_LAYERS = ('relu1_1', 'relu2_1', 'relu3_1', 'relu4_1', 'relu5_1')


try:
    reduce
except NameError:
    from functools import reduce


def stylize(network, initial, content, styles, iterations,
        content_weight, style_weight, style_blend_weights, tv_weight,
        learning_rate, print_iterations=1, checkpoint_iterations=None):
    """
    Stylize images.
    This function yields tuples (iteration, image); `iteration` is None
    if this is the final image (the last iteration).  Other tuples are yielded
    every `checkpoint_iterations` iterations.
    :rtype: iterator[tuple[int|None,image]]
    """
    shape = (1,) + content.shape
    style_shapes = [(1,) + style.shape for style in styles]
    content_features = {}
    style_features = [{} for _ in styles]

    # preprocess for content 
    image_c = content
    image_c = np.add.reduce(image_c,keepdims=True,axis=2)
    image_c = image_c/3
    shape_c = image_c.shape
    print(image_c.shape)
    image_c = image_c.reshape(shape_c[0],shape_c[1])
    image_c = image_c.astype('float32')
        
    # compute content features in feedforward mode
    g = tf.Graph()
    with g.as_default(), g.device('/gpu:0'), tf.Session() as sess:
        image = tf.placeholder('float', shape=shape)
        net, mean_pixel = vgg.net(network, image)
        content_pre = np.array([vgg.preprocess(content, mean_pixel)])
        
        #gatys way
        content_features[CONTENT_LAYER] = net[CONTENT_LAYER].eval(feed_dict={image: content_pre})
        
       
    # compute style features in feedforward mode
    for i in range(len(styles)):
        g = tf.Graph()
        with g.as_default(), g.device('/gpu:0'), tf.Session() as sess:
            image = tf.placeholder('float32', shape=style_shapes[i])
            net, mean_pixel = vgg.net(network, image)
            style_pre = np.array([vgg.preprocess(styles[i], mean_pixel)])
            for layer in STYLE_LAYERS:
                features = net[layer].eval(feed_dict={image: style_pre})
                #_, height, width, number =features.shape
                #size = height*width*number**2
                features = np.reshape(features, (-1, features.shape[3]))

                gram = np.matmul(features.T, features) / features.size
                style_features[i][layer] = gram

    # make stylized image using backpropogation
    with tf.Graph().as_default():

        sobel_x = tf.constant([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], tf.float32)
        sobel_x_filter = tf.reshape(sobel_x, [3, 3, 1, 1])
        sobel_y_filter = tf.transpose(sobel_x_filter, [1, 0, 2, 3])
        
        image_resized_ori = tf.expand_dims((tf.expand_dims(image_c, 0)),3)
        filtered_x_ori = tf.nn.conv2d(image_resized_ori, sobel_x_filter,
                          strides=[1, 1, 1, 1], padding='SAME')
        filtered_y_ori = tf.nn.conv2d(image_resized_ori, sobel_y_filter,
                          strides=[1, 1, 1, 1], padding='SAME')
        mag = tf.add(filtered_x_ori**2,filtered_y_ori**2)
        mag = tf.sqrt(mag)
        mag = tf.nn.sigmoid(mag)

        if initial is None:
            noise = np.random.normal(size=shape, scale=np.std(content) * 0.1)
            initial = tf.random_normal(shape) * 0.256
        else:
            initial = np.array([vgg.preprocess(initial, mean_pixel)])
            initial = initial.astype('float32')
        image = tf.Variable(initial)
        net, _ = vgg.net(network, image)

        # content loss(gatys)
        content_loss_g = content_weight * (2 * tf.nn.l2_loss(
                net[CONTENT_LAYER] - content_features[CONTENT_LAYER]) /
                content_features[CONTENT_LAYER].size)

        #content loss (our way)
        image_n = image
        image_n = tf.reduce_sum(image_n,3)
        image_n = tf.div(image_n,3)
        image_n = tf.expand_dims(image_n,3)
        image_resized = image_n
        #iamge_resized = image_resized.astype('float64')
        filtered_x = tf.nn.conv2d(image_resized, sobel_x_filter,
                          strides=[1, 1, 1, 1], padding='SAME')
        filtered_y = tf.nn.conv2d(image_resized, sobel_y_filter,
                          strides=[1, 1, 1, 1], padding='SAME')
    
        mag_r = tf.add(filtered_x**2,filtered_y**2)
        mag_r = tf.sqrt(mag_r)
        mag_r = tf.nn.sigmoid(mag_r)
        size = shape_c[0]*shape_c[1]
        #mag_r1 = np.array(mag_r)
        #_, height, width, number = map(mag_r, mag_r.get_shape())
        #size = height * width * number
        loss = (tf.nn.l2_loss(mag-mag_r))
        content_loss_edge = 0*content_weight *2*loss

        # style loss
        style_loss = 0
        for i in range(len(styles)):
            style_losses = []
            for style_layer in STYLE_LAYERS:
                layer = net[style_layer]
                _, height, width, number = map(lambda i: i.value, layer.get_shape())
                size = height * width * number
                feats = tf.reshape(layer, (-1, number))
                gram = tf.matmul(tf.transpose(feats), feats) / size
                style_gram = style_features[i][style_layer]
                #_, height, width, number = style_gram.shape
                #style_nor_size = height*width*number**2
                #print(style_gram.shape)
                style_losses.append(2 * tf.nn.l2_loss(gram - style_gram) / style_gram.size)
            style_loss += style_weight * style_blend_weights[i] * reduce(tf.add, style_losses)
        # total variation denoising
        tv_y_size = _tensor_size(image[:,1:,:,:])
        tv_x_size = _tensor_size(image[:,:,1:,:])
        tv_loss = tv_weight * 2 * (
                (tf.nn.l2_loss(image[:,1:,:,:] - image[:,:shape[1]-1,:,:]) /
                    tv_y_size) +
                (tf.nn.l2_loss(image[:,:,1:,:] - image[:,:,:shape[2]-1,:]) /
                    tv_x_size))
        # overall loss
        loss = content_loss_edge + style_loss + tv_loss + content_loss_g

        # optimizer setup
        train_step = tf.train.AdamOptimizer(learning_rate).minimize(loss)

        def print_progress(i, last=False):
            stderr.write('Iteration %d/%d\n' % (i + 1, iterations))
            stderr.write('  content loss_g: %g\n' % content_loss_g.eval())
            stderr.write('  content loss_edge: %g\n' % content_loss_edge.eval())
            stderr.write('    style loss: %g\n' % style_loss.eval())
            stderr.write('       tv loss: %g\n' % tv_loss.eval())
            stderr.write('    total loss: %g\n' % loss.eval())
            if last or (print_iterations and i % print_iterations == 0):
                stderr.write('Iteration %d/%d\n' % (i + 1, iterations))
                stderr.write('  content loss_g: %g\n' % content_loss_g.eval())
                stderr.write('  content loss_edge: %g\n' % content_loss_edge.eval())
                stderr.write('    style loss: %g\n' % style_loss.eval())
                stderr.write('       tv loss: %g\n' % tv_loss.eval())
                stderr.write('    total loss: %g\n' % loss.eval())

        # optimization
        best_loss = float('inf')
        best = None
        with tf.Session() as sess:
            sess.run(tf.global_variables_initializer())
            for i in range(iterations):
                last_step = (i == iterations - 1)
                print_progress(i, last=last_step)
                train_step.run()

                if (checkpoint_iterations and i % checkpoint_iterations == 0) or last_step:
                    this_loss = loss.eval()
                    if this_loss < best_loss:
                        best_loss = this_loss
                        best = image.eval()
                    yield (
                        (None if last_step else i),
                        vgg.unprocess(best.reshape(shape[1:]), mean_pixel)
                    )


def _tensor_size(tensor):
    from operator import mul
    return reduce(mul, (d.value for d in tensor.get_shape()), 1)