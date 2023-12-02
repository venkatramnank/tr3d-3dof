# Tools for Physion data

import PIL.Image as Image
from PIL import ImageOps
import numpy as np
import math
import io
import h5py
import os
import open3d as o3d
import matplotlib.pyplot as plt
from scipy.spatial.transform import Rotation as R


class PhysionPointCloudGenerator:
    """Point cloud generator for a single frame 
    """

    def __init__(self, hdf5_file_path, frame_number, plot=False):
        self.hdf5_file_path = hdf5_file_path
        if not isinstance(frame_number, str):
            self.frame_number = '{:04d}'.format(frame_number)
        else:
            self.frame_number = frame_number
        self.hf = h5py.File(self.hdf5_file_path, 'r')

        self.object_ids = self.hf["static"]["object_ids"][:].tolist()
        self.object_names = self.hf["static"]["model_names"][:]
        self.scales = self.hf["static"]["scale"][:]
        self.colors = self.hf['static']['color'][:]
        self.segmentation_colors = self.hf['static']['object_segmentation_colors'][:]
        self.projection_matrix = np.array(
            self.hf['frames'][self.frame_number]['camera_matrices']['projection_matrix']).reshape(4, 4)
        self.camera_matrix = np.array(
            self.hf['frames'][self.frame_number]['camera_matrices']['camera_matrix']).reshape(4, 4)

        self.img_array = self.io2image(
            self.hf["frames"][self.frame_number]["images"]["_img"][:])
        self.img_height = self.img_array.shape[0]
        self.img_width = self.img_array.shape[1]
        self.seg_array = self.io2image(
            self.hf["frames"][self.frame_number]["images"]["_id"][:])
        self.dep_array = self.get_depth_values(
            self.hf["frames"][self.frame_number]["images"]["_depth"][:], width=self.img_width, height=self.img_height, near_plane=0.1, far_plane=100)
        self.positions = self.hf["frames"][self.frame_number]["objects"]["positions"][:]
        self.rotations = self.hf["frames"][self.frame_number]["objects"]["rotations"][:]
        self.plot = plot
        
    def io2image(self, tmp):
        """Converts bytes format to numpy array of image

        Args:
            tmp (numpy.ndarray): Bytes array of image

        Returns:
            numpy.ndarray: H x W x 3 image
        """
        image = Image.open(io.BytesIO(tmp))
        # image = ImageOps.mirror(image)

        image_array = np.array(image)
        return image_array

    def get_depth_values(self, image: np.array, depth_pass: str = "_depth", width: int = 256, height: int = 256, near_plane: float = 0.1, far_plane: float = 100) -> np.array:
        """
        Get the depth values of each pixel in a _depth image pass.
        The far plane is hardcoded as 100. The near plane is hardcoded as 0.1.
        (This is due to how the depth shader is implemented.)
        :param image: The image pass as a numpy array.
        :param depth_pass: The type of depth pass. This determines how the values are decoded. Options: `"_depth"`, `"_depth_simple"`.
        :param width: The width of the screen in pixels. See output data `Images.get_width()`.
        :param height: The height of the screen in pixels. See output data `Images.get_height()`.
        :param near_plane: The near clipping plane. See command `set_camera_clipping_planes`. The default value in this function is the default value of the near clipping plane.
        :param far_plane: The far clipping plane. See command `set_camera_clipping_planes`. The default value in this function is the default value of the far clipping plane.
        :return An array of depth values.
        """
        # image = np.flip(np.reshape(image, (height, width, 3)), 1)
        image = np.reshape(image, (height, width, 3))

        # Convert the image to a 2D image array.
        if depth_pass == "_depth":
            depth_values = np.array(
                (image[:, :, 0] + image[:, :, 1] / 256.0 + image[:, :, 2] / (256.0 ** 2)))
        elif depth_pass == "_depth_simple":
            depth_values = image[:, :, 0] / 256.0
        else:
            raise Exception(f"Invalid depth pass: {depth_pass}")
        # Un-normalize the depth values.
        return (depth_values * ((far_plane - near_plane) / 256.0)).astype(np.float32)

    def get_intrinsics_from_projection_matrix(self, proj_matrix, size=(256, 256)):
        """Gets intrisic matrices

        Args:
            proj_matrix (np.array): Projection matrix
            size (tuple, optional): Size of image. Defaults to (256, 256).

        Returns:
            numpy.ndarray, float, int: pixel to camera projection, focal length, sensor width
        """
        H, W = size
        vfov = 2.0 * math.atan(1.0/proj_matrix[1][1]) * 180.0 / np.pi
        vfov = vfov / 180.0 * np.pi
        tan_half_vfov = np.tan(vfov / 2.0)
        tan_half_hfov = tan_half_vfov * H / float(H)
        fx = W / 2.0 / tan_half_hfov  # focal length in pixel space
        fy = H / 2.0 / tan_half_vfov
        fl = fx
        sw = 1
        pix_T_cam = np.array([[fx, 0, W / 2.0],
                              [0, fy, H / 2.0],
                              [0, 0, 1]])
        return pix_T_cam, fl, sw

    def depth_to_z(self, z, focal_length, sensor_width):
        """calculates and returns the corresponding 3D coordinates in the camera space.

        Args:
            z (numpy.ndarray):  A 3D array representing depth values
            focal_length (float): focal length
            sensor_width (int): sensor width

        Returns:
            numpy.ndarray: 3D coordinates
        """
        z = np.array(z)
        assert z.ndim >= 3
        h, w, _ = z.shape[-3:]
        pixel_centers_x = (
            np.arange(-w/2, w/2, dtype=np.float32) + 0.5) / w * sensor_width
        pixel_centers_y = (
            np.arange(-h/2, h/2, dtype=np.float32) + 0.5) / h * sensor_width
        squared_distance_from_center = np.sum(np.square(np.meshgrid(
            pixel_centers_x,  # X-Axis (columns)
            pixel_centers_y,  # Y-Axis (rows)
            indexing="xy",
        )), axis=0)

        depth_scaling = np.sqrt(
            1 + squared_distance_from_center / focal_length**2)
        depth_scaling = depth_scaling.reshape(
            (1,) * (z.ndim - 3) + depth_scaling.shape + (1,))

        return z / depth_scaling

    def meshgrid2d_py(self, Y, X):
        grid_y = np.linspace(0.0, Y-1, Y)
        grid_y = np.reshape(grid_y, [Y, 1])
        grid_y = np.tile(grid_y, [1, X])

        grid_x = np.linspace(0.0, X-1, X)
        grid_x = np.reshape(grid_x, [1, X])
        grid_x = np.tile(grid_x, [Y, 1])

        return grid_y, grid_x

    def extract_rgbd_from_physion_frame(self, frame_images):
        frame_image = np.array(frame_images.get('_img'))
        rgb_frame_array = self.io2image(frame_image)
        return rgb_frame_array

    def extract_depth_from_physion_frame(self, frame_images):
        frame_depth_array = np.array(frame_images.get('_depth'))
        return frame_depth_array

    def convert_2D_to_3D(self, obj_2D, camera_matrix, projection_matrix, target_resolution=(256, 256)):
        """
        Convert 2D coordinates to 3D coordinates using camera and projection matrices.

        Args:
            obj_2D (numpy.ndarray): Array of 2D coordinates with shape (num_points, 3),
                                    where each row represents (x, y, depth).
            camera_matrix (numpy.ndarray): Camera matrix for the 3D-to-2D projection.
            projection_matrix (numpy.ndarray): Projection matrix for transforming normalized device coordinates.
            target_resolution (tuple, optional): Target resolution of the 2D coordinates. Defaults to (256, 256).

        Returns:
            numpy.ndarray: Array of transformed 3D coordinates with shape (num_points, 3).
        """
        obj_num = obj_2D.shape[0]
        obj_2D = np.concatenate([obj_2D[:, 1:2],
                                obj_2D[:, 0:1],
                                obj_2D[:, 2:3],
                                 ], axis=1).astype(np.float32)

        obj_2D[:, 1] = 1 - obj_2D[:, 1]/target_resolution[1]
        obj_2D[:, 0] = obj_2D[:, 0]/target_resolution[0]
        obj_2D[:, :2] = obj_2D[:, :2] * 2 - 1

        obj_3D = np.concatenate([obj_2D[:, :2] * obj_2D[:, 2:3],
                                 obj_2D[:, 2:3],
                                (obj_2D[:, 2:3] - 1.0 *
                                 projection_matrix[2, 3])
                                / projection_matrix[2, 2] * projection_matrix[3, 2]],
                                axis=1)

        obj_3D = np.linalg.inv(projection_matrix) @ obj_3D.T

        obj_3D = (np.linalg.inv(camera_matrix) @ obj_3D).T
        return obj_3D[:, :3]

    def background_pc(self, size, ind_i_all, ind_j_all, true_z_f, rgb_f, camera_matrix, projection_matrix):
        """
        Generate a point cloud representing the background of a scene based on input parameters.

        Args:
            size (int): Size of the grid (assumes a square grid).
            ind_i_all (list): List of 1D arrays containing row indices for each grid cell.
            ind_j_all (list): List of 1D arrays containing column indices for each grid cell.
            true_z_f (numpy.ndarray): 2D array representing the true depth values for each pixel.
            rgb_f (numpy.ndarray): 3D array representing the RGB values for each pixel.
            camera_matrix (numpy.ndarray): Camera matrix for the transformation from image to camera coordinates.
            projection_matrix (numpy.ndarray): Projection matrix for the transformation from camera to world coordinates.

        Returns:
            tuple: A tuple containing two elements:
                - background_depth_point_world (numpy.ndarray): 2D array representing the 3D coordinates of background points.
                - background_rgb_value (numpy.ndarray): 2D array representing the RGB values of background points.
        """
        def calArray2dDiff(array_0, array_1):
            array_0_rows = array_0.view(
                [('', array_0.dtype)] * array_0.shape[1])
            array_1_rows = array_1.view(
                [('', array_1.dtype)] * array_1.shape[1])

            return np.setdiff1d(array_0_rows, array_1_rows).view(array_0.dtype).reshape(-1, array_0.shape[1])

        ind_i_all = np.concatenate(ind_i_all, axis=0)
        ind_j_all = np.concatenate(ind_j_all, axis=0)
        ind_o_all = np.concatenate(
            (ind_i_all[:, np.newaxis], ind_j_all[:, np.newaxis]), axis=1)

        i_all = np.concatenate(
            [np.ones(size).astype(int) * i for i in range(size)])
        j_all = np.concatenate([np.arange(size).astype(int)
                               for i in range(size)])
        ind_all = np.concatenate(
            (i_all[:, np.newaxis], j_all[:, np.newaxis]), axis=1)

        ind_b_all = calArray2dDiff(ind_all, ind_o_all)

        background_z_value = true_z_f[ind_b_all[:, 0], ind_b_all[:, 1]]
        background_rgb_value = rgb_f[ind_b_all[:, 0], ind_b_all[:, 1], :]
        background_depth_point_img = np.concatenate(
            [ind_b_all[:, 0][:, np.newaxis], ind_b_all[:, 1][:, np.newaxis], background_z_value[:, np.newaxis]], 1)
        background_depth_point_world = self.convert_2D_to_3D(
            background_depth_point_img, camera_matrix, projection_matrix, target_resolution=(256, 256))

        return background_depth_point_world, background_rgb_value

    
    def pcd_visualizer_with_color(self, pcd_array, color_array):
        pcd = o3d.geometry.PointCloud()
        pcd.points = o3d.utility.Vector3dVector(pcd_array)
        pcd.colors = o3d.utility.Vector3dVector(color_array)
        o3d.visualization.draw_geometries([pcd])
    
    def run(self):
        """Builds point cloud from depth images and rgb images

        Returns:
            np.array: PCD [points x 6]; xyzrgb
        """

        obj_depth_point_world_f = []
        obj_partial_rgb_f = []
        ind_i_all, ind_j_all = [], []

        for idx, obj_id in enumerate(self.object_ids):
            # obj_name = self.hf['static']['model_names'][:][idx]
            # if obj_name in self.hf['static']['distractors'][:]:
            #     continue
            # if obj_name in self.hf['static']['occluders'][:]:
            #     continue

            selected_mask = np.logical_and.reduce(
                (
                    self.seg_array[:, :,
                                   0] == self.segmentation_colors[idx, 0],
                    self.seg_array[:, :,
                                   1] == self.segmentation_colors[idx, 1],
                    self.seg_array[:, :,
                                   2] == self.segmentation_colors[idx, 2],
                )
            )

            if np.sum(selected_mask) == 0:
                continue

            ind_i, ind_j = np.nonzero(selected_mask)
            ind_i_all.append(ind_i)
            ind_j_all.append(ind_j)
            z_value = self.dep_array[ind_i, ind_j]
            obj_depth_point_img = np.concatenate(
                [ind_i[:, np.newaxis], ind_j[:, np.newaxis], z_value[:, np.newaxis]], 1).astype(np.float32)
            obj_rgb_value = self.img_array[ind_i, ind_j, :]

            obj_depth_point_world = self.convert_2D_to_3D(obj_depth_point_img, self.camera_matrix, self.projection_matrix, target_resolution=(
                self.img_array.shape[0], self.img_array.shape[1]))

            obj_depth_point_world_f.append(obj_depth_point_world)
            obj_partial_rgb_f.append(obj_rgb_value)

        obj_depth_point_world_f = np.concatenate(
            obj_depth_point_world_f, axis=0)
        obj_partial_rgb_f = np.concatenate(obj_partial_rgb_f, axis=0)

        background_depth_point_world, background_rgb_value = self.background_pc(self.img_array.shape[0],
                                                                                ind_i_all,
                                                                                ind_j_all,
                                                                                self.dep_array,
                                                                                self.img_array,
                                                                                self.camera_matrix,
                                                                                self.projection_matrix)
        complete_pcd_world = np.concatenate(
            [obj_depth_point_world_f, background_depth_point_world], axis=0)
        complete_pcd_colors = np.concatenate(
            [obj_partial_rgb_f, background_rgb_value], axis=0)
        complete_pcd_colors = complete_pcd_colors/255.
        complete_pcd = np.concatenate(
            [complete_pcd_world, complete_pcd_colors], axis=1)

        if self.plot:
            self.pcd_visualizer_with_color(complete_pcd_world, complete_pcd_colors)
        
        return complete_pcd


def bbox_3d_visualizer(points, bbox_params, bbox_color=(0, 1, 0), rot_axis=2, center_mode=None):
    """
    Draw bbox on visualizer and change the color of points inside bbox3d.

    Args:
        pcd (:obj:`open3d.geometry.PointCloud`): point cloud of shape (points x 6) representing xyzrgb.
        bbox_params (list): 3d bbox (x, y, z, x_size, y_size, z_size, yaw) to visualize.
        bbox_color (tuple[float], optional): the color of bbox.
            Default: (0, 1, 0).
        rot_axis (int, optional): rotation axis of bbox. Default: 2.
        center_mode (bool, optional): indicate the center of bbox is
            bottom center or gravity center. available mode
            ['lidar_bottom', 'camera_bottom']. Default: None.
    """
    vis = o3d.visualization.Visualizer()
    vis.create_window()
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points[:, :3])
    pcd.colors = o3d.utility.Vector3dVector(points[:, 3:])
    vis.add_geometry(pcd)
    for i in range(len(bbox_params)):
        center = bbox_params[i, 0:3]
        dim = bbox_params[i, 3:6]
        yaw = np.zeros(3)
        yaw[rot_axis] = bbox_params[i, 6]
        rot_mat = o3d.geometry.get_rotation_matrix_from_xyz(yaw)

        if center_mode == 'lidar_bottom':
            center[rot_axis] += dim[rot_axis] / 2  # bottom center to gravity center
        elif center_mode == 'camera_bottom':
            center[rot_axis] -= dim[rot_axis] / 2  # bottom center to gravity center

        box3d = o3d.geometry.OrientedBoundingBox(center, rot_mat, dim)

        line_set = o3d.geometry.LineSet.create_from_oriented_bounding_box(box3d)
        line_set.paint_uniform_color(bbox_color)
        # draw bboxes on visualizer
        vis.add_geometry(line_set)


    # update points colors
    vis.update_geometry(pcd)
    vis.run()
    # Close the visualizer window
    vis.destroy_window()