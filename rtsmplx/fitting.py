import torch
import pytorch3d
import torch.nn as nn
import torch.optim as optim
import numpy as np
import rtsmplx.dataset as dataset
import rtsmplx.body_model as bm
import rtsmplx.camera as cam
import rtsmplx.utils as utils
import rtsmplx.lm_joint_mapping
import pytorch3d.structures
import trimesh
from torch.utils.tensorboard import SummaryWriter


def opt_step(
    image_landmarks,
    pose_image_landmarks,
    face_image_landmarks,
    body_model,
    opt,
    ocam,
    body_params=None,
    lr=1e-3,
    regularization=1e-3,
    body=False,
    face=False,
    hands=False,
    writer=None,
    idx=0,
):
    pose_mapping = rtsmplx.lm_joint_mapping.get_lm_mapping()
    """
    pose_image_landmarks = torch.cat(
        (pose_image_landmarks, face_image_landmarks), dim=0
    )
    """

    if body == True:
        """
        if body_params == None:
            body_pose_params = body_model.body_pose
        else:
            body_pose_params = body_params
        body_pose_params.requires_grad = True
        """
        # joints = body_model.get_joints(body_pose=body_pose_params)
        joints = body_model.get_joints(body_pose=body_model.body_pose)
        joints = joints[pose_mapping[:, 0]]
        pose_prediction = ocam.forward(joints)
        pose_loss_pred = pose_loss(pose_prediction, pose_image_landmarks)
        if writer != None:
            writer.add_scalar("Pose Loss", pose_loss_pred.detach(), idx)
    else:
        pose_loss_pred = 0

    if face == True:
        bary_coords = body_model.bary_coords
        bary_coords.requires_grad = True
        bary_vertices = body_model.bary_vertices
        transf_bary_coords = transform_bary_coords(bary_coords, bary_vertices)
        face_predictions = ocam.forward(transf_bary_coords)
        face_loss_pred = face_loss(face_predictions, face_image_landmarks)
        if writer != None:
            writer.add_scalar("Face Loss", face_loss_pred.detach(), idx)
    else:
        face_loss_pred = 0

    if hands == True:
        # Work in progress
        hands_loss_pred = 0
        if writer != None:
            writer.add_scalar("Hands Loss", hands_loss_pred.detach(), idx)
    else:
        hands_loss_pred = 0
    body_pose_param = body_model.body_pose
    loss_pred = loss(pose_loss_pred, face_loss_pred, hands_loss_pred, body_pose_param, regularization)
    opt.zero_grad()
    loss_pred.backward()
    opt.step()
    return (body_model, ocam)


def run(
    num_runs,
    landmarks,
    pose_image_landmarks,
    face_image_landmarks,
    body_model,
    opt,
    ocam,
    body=False,
    face=False,
    hands=False,
    body_params=None,
    lr=1e-3,
    regularization=1e-3,
    print_every=50,
):
    writer = SummaryWriter()
    for i in range(1, num_runs):
        # if i % print_every == 0:
            # print(i)

        body_model, ocam = opt_step(
            landmarks,
            pose_image_landmarks,
            face_image_landmarks,
            body_model,
            opt,
            ocam,
            body=body,
            face=face,
            hands=hands,
            body_params=body_params,
            lr=lr,
            regularization=regularization,
            writer=writer,
            idx=i,
        )
    writer.close()
    return body_model, ocam


def opt_loop(
    data,
    body_model,
    num_runs,
    body=False,
    face=False,
    hands=False,
    lr=1e-3,
    regularization=1e-2,
):
    image = data[0]
    landmarks = data[1]
    pose_mapping = rtsmplx.lm_joint_mapping.get_lm_mapping()
    pose_image_landmarks = landmarks.body_landmarks()[pose_mapping[:, 1]]
    face_image_landmarks = landmarks.face_landmarks()[17:, :]
    ocam = cam.OrthographicCamera()
    # opt = optimizer(body_model.parameters(), lr=lr)
    opt = optimizer(ocam.parameters(), lr=lr)
    # opt = optimizer(list(body_model.parameters()) + list(ocam.parameters()), lr=lr)
    body_model, ocam = run(
        num_runs,
        landmarks,
        pose_image_landmarks,
        face_image_landmarks,
        body_model,
        opt,
        ocam,
        body=body,
        face=face,
        hands=hands,
        body_params=None,
        lr=lr,
        regularization=regularization,
    )
    # opt = optimizer(ocam.parameters(), lr=lr)
    opt = optimizer(body_model.parameters(), lr=lr)
    body_model, ocam = run(
        num_runs,
        landmarks,
        pose_image_landmarks,
        face_image_landmarks,
        body_model,
        opt,
        ocam,
        body=body,
        face=face,
        hands=hands,
        body_params=None,
        lr=lr,
        regularization=regularization,
    )

    lr = lr * 0.1
    opt = optimizer(list(body_model.parameters()) + list(ocam.parameters()), lr=lr)
    body_model, ocam = run(
        num_runs,
        landmarks,
        pose_image_landmarks,
        face_image_landmarks,
        body_model,
        opt,
        ocam,
        body=body,
        face=face,
        hands=hands,
        body_params=None,
        lr=lr,
        regularization=regularization,
    )
    body_pose_params = body_model.body_pose

    return body_model, body_pose_params, ocam


def get_mesh(body_model, body_pose):
    faces = body_model.faces.reshape(-1, 3).detach().numpy()
    vertices = (
        body_model.forward(body_pose=body_pose).vertices.reshape(-1, 3).detach().numpy()
    )
    tri_mesh = trimesh.base.Trimesh(vertices=vertices, faces=faces)
    return tri_mesh


def pose_loss(joint_coords_2d, landmarks_2d):
    loss_func = mse_loss()
    return loss_func(joint_coords_2d, landmarks_2d)


def face_loss(bary_coords_2d, landmarks_2d):
    return nn.MSELoss(bary_coords_2d, landmarks_2d)


def loss(pose_loss, face_loss, hands_loss, body_pose, regularization):
    body_pose_prior = torch.linalg.norm(body_pose)
    loss_val = pose_loss + face_loss + hands_loss + regularization * body_pose_prior
    return loss_val


def mse_loss():
    return nn.MSELoss()


def optimizer(params, lr=1e-3):
    return optim.Adam(params, lr=lr)


def transform_bary_coords(bary_coords, bary_vertices):
    transf_bary_coords = torch.einsum("ijk,ij->ik", bary_vertices, bary_coords)
    return transf_bary_coords


def plot_landmarks(ocam, body_model, image_landmarks):
    pose_mapping = rtsmplx.lm_joint_mapping.get_lm_mapping()
    pose_image_landmarks = image_landmarks.body_landmarks()
    face_image_landmarks = image_landmarks.face_landmarks()[17:, :]
    pose_image_landmarks = torch.cat(
        (pose_image_landmarks, face_image_landmarks), dim=0
    )
    pose_image_landmarks = pose_image_landmarks[pose_mapping[:, 1]]

    joints = body_model.get_joints(body_pose=body_model.body_pose)
    joints = joints[pose_mapping[:, 0]]
    pose_prediction = ocam.forward(joints)
    return (pose_image_landmarks.numpy(), pose_prediction.detach().numpy())


if __name__ == "__main__":
    pass
